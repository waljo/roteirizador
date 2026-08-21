from __future__ import annotations

import csv
from datetime import date, datetime
from itertools import combinations
import json
from pathlib import Path
import re
import time
import unicodedata
from typing import List, Optional

from .domain import (
    AppConfig,
    AvailableBoat,
    DemandItem,
    ExtratoOriginConfig,
    FleetVessel,
    OperationalConfig,
    OperationMetadata,
    OperationVersion,
    PickupBoatState,
    PickupDemand,
    SolverRunResult,
    TideAlertAnalysis,
    TideTableConfig,
    VersionBundle,
    VERSION_CL,
    VERSION_PROGRAMACAO,
)
from .route_syntax import parse_route_text
from .services import AppService, default_operation_version, today_iso
from .offshore_pd.aliases import AliasResolver
from .passenger_manifest import (
    AssignmentIssue,
    DeliveryRecord,
    PassengerAssignment,
    PickupListResult,
    TransferRecord,
    VesselItinerary,
    build_passenger_pickup_list,
    build_passenger_positions,
    export_assignments_csv,
    format_assignments_text,
    parse_cl_itinerary_text,
    read_petrobras_delivery_pdf,
    read_petrobras_transfer_pdf,
)

LAYOUT_SPEC_VERSION = "1.4.0"
HELP_SECTION_MAX_HEIGHT = 220
BUTTON_GRID_SPACING = 6
USE_COLLAPSIBLE_SPLITTERS = False

ROUTE_HELP_TEXT = """COMO DIGITAR A ROTA:

Cada parada e separada por "/".
O primeiro token de cada trecho e sempre a plataforma.

REGRAS:
+N = embarca N pax na plataforma atual
-N = desembarca N pax vindos do TMIB
-ORIGEM:N = desembarca N pax vindos da origem informada

EXEMPLOS:
TMIB +22/M10 -5/M9 -7 +4/M6 -4 +1/B2 -4 -M9:4/B1 -2 -M6:1
M6 +11/M10 +9/M9 +10 -M6:11 -M10:9/M6 -M9:10

IMPORTANTE:
- use "/" para separar paradas
- use ":" apenas em drops com origem explicita
- M1:-3 esta errado; o correto e -M1:3
"""

DEMAND_HELP_TEXT = """DICA DE PREENCHIMENTO DA DEMANDA

Apos importar a demanda, revise os dados antes de gerar a distribuicao.

Ajustes obrigatorios apos importacao:
- Padronize plataformas com sufixo de turno.
  Ex.: PGA3 (D) e PGA3 (N) devem ser ajustadas para PGA3.
- Remova a linha SPH-02 da tabela de demanda.
  Esse atendimento deve ser lancado como rota fixa.
  Ao cadastrar a rota fixa, use M6 (nao SPH-02).

Importante sobre o escopo do roteirizador:
- O roteirizador otimiza principalmente a demanda de saida de passageiros do TMIB.
- A coluna M1 do extrato e preservada na grade, mas a geracao automatica ainda exige que essa demanda
  esteja coberta por rotas fixas.
- Rotas Fixas com saida ate 15:00 abatem da demanda automaticamente.
- Rotas Fixas com saida apos 15:00 nao abatem da demanda (apenas aparecem na distribuicao).

Exemplos de Rotas Fixas:
- Operacao iniciando as 05:10 em M6 (troca de turma da sonda e embarque em M10) abate da demanda.
- Operacao iniciando as 17:00 em M9 (troca de turma da sonda) nao abate da demanda.

Em caso de duvida, verifique operacoes de dias anteriores.
"""

try:
    from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Qt, Signal
    from PySide6.QtGui import QColor, QBrush
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QHeaderView,
        QApplication,
        QCheckBox,
        QComboBox,
        QScrollArea,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QSizePolicy,
        QDialog,
        QDialogButtonBox,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
        QInputDialog,
    )
except ImportError as exc:  # pragma: no cover
    class _QtFallback:
        Key_Return = 0
        Key_Enter = 0
        Key_Delete = 0
        Key_Backspace = 0
        UserRole = 0
        Vertical = 0

    def Signal(*_args, **_kwargs):  # type: ignore
        return None

    QEvent = object
    QObject = object
    QThread = object
    QTimer = object
    QAbstractItemView = object
    QHeaderView = object
    Qt = _QtFallback()
    QApplication = None
    QCheckBox = object
    QComboBox = object
    QScrollArea = object
    QFileDialog = object
    QFormLayout = object
    QGridLayout = object
    QGroupBox = object
    QHBoxLayout = object
    QLabel = object
    QLineEdit = object
    QListWidget = object
    QListWidgetItem = object
    QMainWindow = object
    QMessageBox = object
    QPushButton = object
    QProgressBar = object
    QSizePolicy = object
    QDialog = object
    QDialogButtonBox = object
    QSplitter = object
    QTableWidget = object
    QTableWidgetItem = object
    QTabWidget = object
    QTextEdit = object
    QVBoxLayout = object
    QWidget = object
    QInputDialog = object
    QColor = object
    QBrush = object
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


def _make_color(hex_color: str):
    return QBrush(QColor(hex_color))


# Marca de selecao das tabelas de escolha de passageiro. O indicador nativo do Qt e um
# quadradinho pequeno e de baixo contraste — a pedido do operador, a linha marcada passa a
# mostrar um check verde e a linha inteira e clicavel, em vez de so a caixinha.
_CHECK_MARK = "\u2713"
_CHECK_FG = "#1e8449"
_CHECK_BG = "#eafaf1"


def _set_row_checked(table: QTableWidget, row: int, checked: bool) -> None:
    mark = table.item(row, 0)
    if mark is None:
        return
    mark.setData(Qt.UserRole, bool(checked))
    mark.setText(_CHECK_MARK if checked else "")
    font = mark.font()
    font.setBold(True)
    if font.pointSize() > 0:
        font.setPointSize(font.pointSize() + 2)
    mark.setFont(font)
    mark.setForeground(_make_color(_CHECK_FG))
    fundo = _make_color(_CHECK_BG) if checked else QBrush(Qt.NoBrush)
    for col in range(table.columnCount()):
        cell = table.item(row, col)
        if cell is not None:
            cell.setBackground(fundo)


def _is_row_checked(table: QTableWidget, row: int) -> bool:
    mark = table.item(row, 0)
    return bool(mark is not None and mark.data(Qt.UserRole))


def _setup_check_column(table: QTableWidget) -> None:
    """Coluna 0 estreita e de largura fixa, com o proprio check no cabecalho."""
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.Fixed)
    table.setColumnWidth(0, 34)


class AutoAppendTableWidget(QTableWidget):
    def __init__(self, rows: int, cols: int, parent: Optional[QWidget] = None):
        super().__init__(rows, cols, parent)
        self._append_row_callback = None
        self._remove_row_callback = None
        self._block_delete_backspace = False
        if hasattr(self.horizontalHeader(), "setSectionResizeMode"):
            self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    def set_append_row_callback(self, callback) -> None:
        self._append_row_callback = callback

    def set_remove_row_callback(self, callback) -> None:
        self._remove_row_callback = callback

    def set_block_delete_backspace(self, enabled: bool) -> None:
        self._block_delete_backspace = bool(enabled)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            current_row = self.currentRow()
            super().keyPressEvent(event)
            if current_row < 0:
                return
            if current_row == self.rowCount() - 1 and self._append_row_callback is not None:
                self._append_row_callback()
            next_row = min(current_row + 1, self.rowCount() - 1)
            self.setCurrentCell(next_row, 0)
            item = self.item(next_row, 0)
            if item is not None:
                self.editItem(item)
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self._block_delete_backspace:
                # Bloqueia remocao por teclado, mas preserva a edicao normal da celula.
                super().keyPressEvent(event)
                return
            if self._remove_row_callback is not None:
                self._remove_row_callback(self)
                return
        super().keyPressEvent(event)


class CollapsibleSection(QWidget):
    def __init__(self, title: str, expanded: bool = True, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.toggle_button = QPushButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.clicked.connect(self._apply_state)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(4)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.content)
        self.scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.scroll_area)

        self._title = title
        self._apply_state()

    def add_widget(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.content_layout.addLayout(layout)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle_button.setChecked(expanded)
        self._apply_state()

    def _apply_state(self) -> None:
        expanded = self.toggle_button.isChecked()
        marker = "▼" if expanded else "▶"
        self.toggle_button.setText(f"{marker} {self._title}")
        if expanded:
            self.scroll_area.setMinimumHeight(0)
            self.scroll_area.setMaximumHeight(HELP_SECTION_MAX_HEIGHT)
        else:
            self.scroll_area.setMinimumHeight(0)
            self.scroll_area.setMaximumHeight(0)


class SolverProgressDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._frames = ["|", "/", "-", "\\"]
        self._frame_idx = 0
        self._finished = False
        self._started_at = time.monotonic()
        self.spinner_label = QLabel(self._frames[0])
        self.status_label = QLabel("Iniciando processamento...")
        self.elapsed_label = QLabel("Tempo decorrido: 00:00")
        self.progress_bar = QProgressBar()
        self.log_view = QTextEdit()
        self.anim_timer = QTimer(self)
        self._build()

    def _build(self) -> None:
        self.setWindowTitle("Gerando distribuicao")
        self.resize(760, 420)
        self.setModal(True)

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.spinner_label.setFixedWidth(20)
        header.addWidget(self.spinner_label)
        header.addWidget(self.status_label, 1)
        header.addWidget(self.elapsed_label)
        layout.addLayout(header)

        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)

        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        self.anim_timer.setInterval(120)
        self.anim_timer.timeout.connect(self._tick)
        self.anim_timer.start()

    def _tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(self._frames)
        self.spinner_label.setText(self._frames[self._frame_idx])
        elapsed = int(time.monotonic() - self._started_at)
        self.elapsed_label.setText(f"Tempo decorrido: {self._format_elapsed(elapsed)}")

    @staticmethod
    def _format_elapsed(elapsed_seconds: int) -> str:
        minutes, seconds = divmod(max(0, int(elapsed_seconds)), 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def append_message(self, message: str) -> None:
        text = (message or "").strip()
        if not text:
            return
        self.status_label.setText(text)
        self.log_view.append(text)

    def finish(self, success: bool) -> None:
        self._finished = True
        self.anim_timer.stop()
        self.spinner_label.setText("OK" if success else "ER")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1 if success else 0)
        elapsed = int(time.monotonic() - self._started_at)
        self.elapsed_label.setText(f"Tempo decorrido: {self._format_elapsed(elapsed)}")

    def closeEvent(self, event) -> None:
        if self._finished:
            super().closeEvent(event)
            return
        event.ignore()


class TideAlertDialog(QDialog):
    def __init__(self, analysis: TideAlertAnalysis, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.analysis = analysis
        self.apply_requested = False
        self.setWindowTitle("Alerta de Mare")
        self.resize(760, 520)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        intro = QLabel(
            "A distribuicao gerada conflitou com a regra operacional de mare baixa. "
            "Revise a sugestao abaixo antes de decidir se aplica o ajuste automaticamente."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        details = QTextEdit()
        details.setReadOnly(True)
        details.setPlainText(self._build_details_text())
        layout.addWidget(details, 1)

        if self.analysis.coordinator_text.strip():
            coord_label = QLabel("Texto sugerido para o Coordenador")
            coord_label.setWordWrap(True)
            layout.addWidget(coord_label)
            coord_text = QTextEdit()
            coord_text.setReadOnly(True)
            coord_text.setMaximumHeight(110)
            coord_text.setPlainText(self.analysis.coordinator_text.strip())
            layout.addWidget(coord_text)

        button_row = QHBoxLayout()
        copy_btn = QPushButton("Copiar texto")
        copy_btn.clicked.connect(self._copy_text)
        keep_btn = QPushButton("Manter horarios")
        keep_btn.clicked.connect(self.reject)
        apply_btn = QPushButton("Aplicar ajuste")
        apply_btn.setEnabled(bool(self.analysis.suggestions))
        apply_btn.clicked.connect(self._apply)
        button_row.addWidget(copy_btn)
        button_row.addStretch(1)
        button_row.addWidget(keep_btn)
        button_row.addWidget(apply_btn)
        layout.addLayout(button_row)

    def _build_details_text(self) -> str:
        lines: List[str] = []
        if self.analysis.suggestions:
            lines.append("Sugestoes de ajuste:")
            for item in self.analysis.suggestions:
                lines.append(
                    f"- {item.boat_name}: {item.critical_scope} {item.critical_platform}, "
                    f"chegada atual {item.current_arrival} com {item.current_height_m:.2f} m ({item.trend}); "
                    f"saida {item.current_departure} -> {item.suggested_departure}, "
                    f"chegada alvo {item.target_arrival}."
                )
        if self.analysis.warnings:
            if lines:
                lines.append("")
            lines.append("Pendencias para revisao manual:")
            for warning in self.analysis.warnings:
                lines.append(f"- {warning}")
        if not lines:
            lines.append("Nenhum ajuste automatico foi sugerido.")
        return "\n".join(lines)

    def _copy_text(self) -> None:
        text = self.analysis.coordinator_text.strip() or self._build_details_text()
        if QApplication is not None:
            QApplication.clipboard().setText(text)
        QMessageBox.information(self, "Alerta de Mare", "Texto copiado para a area de transferencia.")

    def _apply(self) -> None:
        self.apply_requested = True
        self.accept()


class SolverRunWorker(QObject):
    progress = Signal(str)
    finished = Signal(object, object)
    failed = Signal(str)
    done = Signal()

    def __init__(
        self,
        service: AppService,
        root: str,
        metadata: OperationMetadata,
        version: OperationVersion,
        imported_csv_path: Optional[Path],
    ):
        super().__init__()
        self.service = service
        self.root = root
        self.metadata = metadata
        self.version = version
        self.imported_csv_path = imported_csv_path

    def run(self) -> None:
        try:
            self.progress.emit("Preparando dados para o solver...")
            updated_operation, result = self.service.run_version(
                self.root,
                self.metadata,
                self.version,
                self.imported_csv_path,
                progress_callback=self.progress.emit,
            )
            self.finished.emit(updated_operation, result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class RouteBuilderDialog(QDialog):
    def __init__(self, boat_name: str, initial_route: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.boat_name = boat_name
        self._updating_preview = False
        self.route_table = AutoAppendTableWidget(0, 8)
        self.preview_edit = QTextEdit()
        self.route_text = initial_route.strip()
        self.setWindowTitle(f"Editor de Rota Fixa - {boat_name}")
        self.resize(980, 520)
        self._build()
        self._load_initial_route(initial_route)
        self._update_preview()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        title = QLabel(
            "Preencha as paradas da rota. O texto da sintaxe sera gerado automaticamente."
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        self.route_table.setHorizontalHeaderLabels(
            [
                "Plataforma",
                "Embarque +",
                "Desemb. TMIB -",
                "Desemb. M9 -M9:",
                "Transb. + Destino",
                "Qtd +",
                "Transb. - Origem",
                "Qtd -",
            ]
        )
        self.route_table.set_append_row_callback(self.add_stop_row)
        layout.addWidget(self.route_table)

        row_actions = QHBoxLayout()
        add_btn = QPushButton("Adicionar parada")
        add_btn.clicked.connect(self.add_stop_row)
        remove_btn = QPushButton("Excluir parada selecionada")
        remove_btn.clicked.connect(self._remove_selected_rows)
        row_actions.addWidget(add_btn)
        row_actions.addWidget(remove_btn)
        layout.addLayout(row_actions)

        layout.addWidget(QLabel("Rota gerada:"))
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setMaximumHeight(90)
        layout.addWidget(self.preview_edit)

        actions = QHBoxLayout()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        apply_btn = QPushButton("Aplicar")
        apply_btn.clicked.connect(self._apply)
        actions.addStretch(1)
        actions.addWidget(cancel_btn)
        actions.addWidget(apply_btn)
        layout.addLayout(actions)

        self.route_table.itemChanged.connect(lambda _item: self._update_preview())

    def _load_initial_route(self, route_text: str) -> None:
        self.route_table.setRowCount(0)
        parsed_rows = self._parse_route(route_text)
        if not parsed_rows:
            self.add_stop_row()
            return
        for row_data in parsed_rows:
            self.add_stop_row(row_data)

    @staticmethod
    def _parse_route(route_text: str) -> List[dict]:
        if not route_text.strip():
            return []
        rows: List[dict] = []
        for part in parse_route_text(route_text):
            row = {
                "platform": part.platform,
                "pickup": str(int(part.pickup_qty)),
                "drop_tmib": str(int(part.tmib_drop)),
                "drop_m9": str(int(part.m9_drop)),
                "plus_dest": "",
                "plus_qty": "0",
                "minus_origin": "",
                "minus_qty": "0",
            }
            explicit_drops = [
                (origin, qty)
                for origin, qty in sorted(part.drops_by_origin.items())
                if origin not in {"TMIB", "M9"} and int(qty) > 0
            ]
            if explicit_drops:
                row["minus_origin"] = explicit_drops[0][0]
                row["minus_qty"] = str(int(explicit_drops[0][1]))
            rows.append(row)
        return rows

    def add_stop_row(self, row_data: Optional[dict] = None) -> None:
        row = self.route_table.rowCount()
        self.route_table.insertRow(row)
        data = row_data or {}
        values = [
            data.get("platform", ""),
            data.get("pickup", "0"),
            data.get("drop_tmib", "0"),
            data.get("drop_m9", "0"),
            data.get("plus_dest", ""),
            data.get("plus_qty", "0"),
            data.get("minus_origin", ""),
            data.get("minus_qty", "0"),
        ]
        for col, value in enumerate(values):
            self.route_table.setItem(row, col, QTableWidgetItem(str(value)))
        self._update_preview()

    def _remove_selected_rows(self) -> None:
        selected_rows = sorted({index.row() for index in self.route_table.selectedIndexes()}, reverse=True)
        if not selected_rows and self.route_table.currentRow() >= 0:
            selected_rows = [self.route_table.currentRow()]
        for row in selected_rows:
            self.route_table.removeRow(row)
        if self.route_table.rowCount() == 0:
            self.add_stop_row()
        self._update_preview()

    @staticmethod
    def _to_int_token(value: str) -> int:
        text = (value or "").strip()
        if not text:
            return 0
        if text.isdigit():
            return int(text)
        return 0

    def _build_route_text(self) -> str:
        parts: List[str] = []
        for row in range(self.route_table.rowCount()):
            platform = self._text(row, 0).upper()
            if not platform:
                continue
            pickup = self._to_int_token(self._text(row, 1))
            drop_tmib = self._to_int_token(self._text(row, 2))
            drop_m9 = self._to_int_token(self._text(row, 3))
            plus_dest = self._text(row, 4).upper()
            plus_qty = self._to_int_token(self._text(row, 5))
            minus_origin = self._text(row, 6).upper()
            minus_qty = self._to_int_token(self._text(row, 7))

            tokens = [platform]
            total_pickup = pickup + plus_qty
            if total_pickup > 0:
                tokens.append(f"+{total_pickup}")
            if drop_tmib > 0:
                tokens.append(f"-{drop_tmib}")
            if drop_m9 > 0:
                tokens.append(f"-M9:{drop_m9}")
            if minus_origin and minus_qty > 0:
                if minus_origin == "TMIB":
                    tokens.append(f"-{minus_qty}")
                elif minus_origin == "M9":
                    tokens.append(f"-M9:{minus_qty}")
                else:
                    tokens.append(f"-{minus_origin}:{minus_qty}")
            parts.append(" ".join(tokens))
        return "/".join(parts)

    def _update_preview(self) -> None:
        if self._updating_preview:
            return
        self._updating_preview = True
        try:
            self.preview_edit.setPlainText(self._build_route_text())
        finally:
            self._updating_preview = False

    def _apply(self) -> None:
        self.route_text = self._build_route_text().strip()
        self.accept()

    def _text(self, row: int, col: int) -> str:
        item = self.route_table.item(row, col)
        return item.text().strip() if item else ""


class SavedRoutesDialog(QDialog):
    def __init__(
        self,
        service: AppService,
        root: str,
        current_route: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.service = service
        self.root = root
        self.current_route = current_route
        self.selected_route: Optional[str] = None
        self._routes: List[dict] = []
        self.setWindowTitle("Rotas fixas salvas")
        self.resize(620, 420)
        self._build()
        self._load_routes()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        self.route_list = QListWidget()
        layout.addWidget(self.route_list)

        action_row = QHBoxLayout()
        save_btn = QPushButton("Salvar rota atual como preset")
        save_btn.clicked.connect(self._save_current_as_preset)
        delete_btn = QPushButton("Excluir selecionada")
        delete_btn.clicked.connect(self._delete_selected)
        edit_btn = QPushButton("Editar selecionada")
        edit_btn.clicked.connect(self._edit_selected)
        action_row.addWidget(save_btn)
        action_row.addWidget(edit_btn)
        action_row.addWidget(delete_btn)
        layout.addLayout(action_row)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        insert_btn = QPushButton("Inserir na linha")
        insert_btn.clicked.connect(self._insert_selected)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(insert_btn)
        layout.addLayout(button_row)

    def _load_routes(self) -> None:
        self.route_list.clear()
        self._routes = self.service.load_saved_routes(self.root)
        for route in self._routes:
            item = QListWidgetItem(f"{route['nome']}\n  {route['rota']}")
            self.route_list.addItem(item)

    def _save_current_as_preset(self) -> None:
        if not self.current_route.strip():
            QMessageBox.warning(
                self,
                "Rotas salvas",
                "A rota atual da linha selecionada esta vazia.",
            )
            return
        nome, ok = QInputDialog.getText(
            self,
            "Salvar preset",
            "Nome descritivo para esta rota:",
        )
        if not ok or not nome.strip():
            return
        self._routes = self.service.add_saved_route(
            self.root, nome.strip(), self.current_route.strip()
        )
        self._load_routes()

    def _edit_selected(self) -> None:
        row = self.route_list.currentRow()
        if row < 0 or row >= len(self._routes):
            QMessageBox.warning(
                self,
                "Rotas salvas",
                "Selecione uma rota para editar.",
            )
            return
        current = self._routes[row]
        nome, ok = QInputDialog.getText(
            self,
            "Editar preset",
            "Nome:",
            text=current["nome"],
        )
        if not ok or not nome.strip():
            return
        rota, ok = QInputDialog.getText(
            self,
            "Editar preset",
            "Rota:",
            text=current["rota"],
        )
        if not ok or not rota.strip():
            return
        self._routes = self.service.update_saved_route(
            self.root, row, nome.strip(), rota.strip()
        )
        self._load_routes()

    def _delete_selected(self) -> None:
        row = self.route_list.currentRow()
        if row < 0:
            QMessageBox.warning(
                self,
                "Rotas salvas",
                "Selecione uma rota para excluir.",
            )
            return
        confirm = QMessageBox.question(
            self,
            "Excluir preset",
            f"Excluir a rota '{self._routes[row]['nome']}'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._routes = self.service.delete_saved_route(self.root, row)
        self._load_routes()

    def _insert_selected(self) -> None:
        row = self.route_list.currentRow()
        if row < 0 or row >= len(self._routes):
            QMessageBox.warning(
                self,
                "Rotas salvas",
                "Selecione uma rota para inserir.",
            )
            return
        self.selected_route = self._routes[row]["rota"]
        self.accept()


class ConfigTab(QWidget):
    def __init__(self, service: AppService, parent_window: "MainWindow"):
        super().__init__()
        self.service = service
        self.parent_window = parent_window
        self.fleet_table = AutoAppendTableWidget(0, 7)
        self.gangway_table = AutoAppendTableWidget(0, 1)
        self.conves_table = AutoAppendTableWidget(0, 1)
        self.extrato_origins_table = AutoAppendTableWidget(0, 4)
        self.tide_limit_edit = QLineEdit()
        self.tide_source_label = QLabel()
        self.tide_year_label = QLabel()
        self.tide_days_label = QLabel()
        self.tide_status_label = QLabel()
        self._tide_table = TideTableConfig()
        self.storage_label = QLabel()
        self._build()

    @staticmethod
    def _configure_section_layout(layout) -> None:
        if hasattr(layout, "setContentsMargins"):
            layout.setContentsMargins(10, 12, 10, 10)
        if hasattr(layout, "setSpacing"):
            layout.setSpacing(10)

    def _build(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(16)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(10)
        self.storage_label.setWordWrap(True)
        form.addRow("Pasta compartilhada", self.storage_label)
        layout.addLayout(form)

        fleet_box = QGroupBox("Frota")
        fleet_layout = QVBoxLayout(fleet_box)
        self._configure_section_layout(fleet_layout)
        self.fleet_table.set_append_row_callback(self.add_fleet_row)
        self.fleet_table.set_remove_row_callback(self.remove_selected_rows)
        self.fleet_table.set_block_delete_backspace(True)
        self.fleet_table.setMinimumHeight(110)
        self.fleet_table.setHorizontalHeaderLabels(
            ["Nome", "Tipo", "Capacidade", "Velocidade", "Aprox. min", "Min/pax", "Ativa"]
        )
        fleet_layout.addWidget(self.fleet_table)
        fleet_btns = QHBoxLayout()
        fleet_btns.setSpacing(8)
        add_fleet = QPushButton("Adicionar embarcacao")
        add_fleet.clicked.connect(self.add_fleet_row)
        remove_fleet = QPushButton("Excluir embarcacao selecionada")
        remove_fleet.clicked.connect(lambda: self.remove_selected_rows(self.fleet_table))
        fleet_btns.addWidget(add_fleet)
        fleet_btns.addWidget(remove_fleet)
        fleet_layout.addLayout(fleet_btns)
        layout.addWidget(fleet_box)

        gangway_box = QGroupBox("Gangway Aqua")
        gangway_layout = QVBoxLayout(gangway_box)
        self._configure_section_layout(gangway_layout)
        self.gangway_table.set_append_row_callback(self.add_gangway_row)
        self.gangway_table.set_block_delete_backspace(True)
        self.gangway_table.setMinimumHeight(92)
        self.gangway_table.setHorizontalHeaderLabels(["Unidade"])
        gangway_layout.addWidget(self.gangway_table)
        add_gangway = QPushButton("Adicionar unidade")
        add_gangway.clicked.connect(self.add_gangway_row)
        gangway_layout.addWidget(add_gangway)
        layout.addWidget(gangway_box)

        conves_box = QGroupBox("Embarcacoes de conves")
        conves_layout = QVBoxLayout(conves_box)
        self._configure_section_layout(conves_layout)
        self.conves_table.set_append_row_callback(self.add_conves_row)
        self.conves_table.set_remove_row_callback(self.remove_selected_rows)
        self.conves_table.set_block_delete_backspace(True)
        self.conves_table.setMinimumHeight(92)
        self.conves_table.setHorizontalHeaderLabels(["Embarcacao"])
        conves_layout.addWidget(self.conves_table)
        conves_btns = QHBoxLayout()
        conves_btns.setSpacing(8)
        add_conves = QPushButton("Adicionar embarcacao de conves")
        add_conves.clicked.connect(self.add_conves_row)
        remove_conves = QPushButton("Excluir embarcacao selecionada")
        remove_conves.clicked.connect(lambda: self.remove_selected_rows(self.conves_table))
        conves_btns.addWidget(add_conves)
        conves_btns.addWidget(remove_conves)
        conves_layout.addLayout(conves_btns)
        layout.addWidget(conves_box)

        extrato_box = QGroupBox("Origens do Extrato")
        extrato_layout = QVBoxLayout(extrato_box)
        self._configure_section_layout(extrato_layout)
        extrato_hint = QLabel(
            "Cadastre aqui as origens canonicas do extrato, se estao ativas e quais aliases devem ser "
            "aceitos ou descartados na importacao do PDF. Use virgula para separar aliases."
        )
        extrato_hint.setWordWrap(True)
        extrato_hint.setStyleSheet("color: #475569;")
        extrato_layout.addWidget(extrato_hint)
        self.extrato_origins_table.set_append_row_callback(self.add_extrato_origin_row)
        self.extrato_origins_table.set_remove_row_callback(self.remove_selected_rows)
        self.extrato_origins_table.set_block_delete_backspace(True)
        self.extrato_origins_table.setMinimumHeight(165)
        self.extrato_origins_table.setHorizontalHeaderLabels(
            ["Codigo", "Ativa", "Aliases aceitos", "Aliases descartados"]
        )
        extrato_layout.addWidget(self.extrato_origins_table)
        extrato_btns = QHBoxLayout()
        extrato_btns.setSpacing(8)
        add_origin = QPushButton("Adicionar origem")
        add_origin.clicked.connect(self.add_extrato_origin_row)
        remove_origin = QPushButton("Excluir origem selecionada")
        remove_origin.clicked.connect(lambda: self.remove_selected_rows(self.extrato_origins_table))
        extrato_btns.addWidget(add_origin)
        extrato_btns.addWidget(remove_origin)
        extrato_layout.addLayout(extrato_btns)
        layout.addWidget(extrato_box)

        tide_box = QGroupBox("Tabua de Mare")
        tide_layout = QVBoxLayout(tide_box)
        self._configure_section_layout(tide_layout)
        tide_hint = QLabel(
            "Importe aqui o PDF anual da tabua de mare. O app reconhece o ano automaticamente, "
            "salva os dias em formato interno e avisa quando a tabela ficar defasada."
        )
        tide_hint.setWordWrap(True)
        tide_hint.setStyleSheet("color: #475569;")
        tide_layout.addWidget(tide_hint)
        tide_form = QFormLayout()
        tide_form.setVerticalSpacing(8)
        tide_form.setHorizontalSpacing(10)
        self.tide_source_label.setWordWrap(True)
        self.tide_year_label.setWordWrap(True)
        self.tide_days_label.setWordWrap(True)
        self.tide_status_label.setWordWrap(True)
        self.tide_limit_edit.setPlaceholderText("0,6")
        tide_form.addRow("Arquivo importado", self.tide_source_label)
        tide_form.addRow("Ano reconhecido", self.tide_year_label)
        tide_form.addRow("Dias carregados", self.tide_days_label)
        tide_form.addRow("Altura limite (m)", self.tide_limit_edit)
        tide_form.addRow("Status", self.tide_status_label)
        tide_layout.addLayout(tide_form)
        tide_btns = QHBoxLayout()
        tide_btns.setSpacing(8)
        import_tide = QPushButton("Selecionar PDF da tabua")
        import_tide.clicked.connect(self.select_tide_pdf)
        tide_btns.addWidget(import_tide)
        tide_btns.addStretch(1)
        tide_layout.addLayout(tide_btns)
        layout.addWidget(tide_box)

        save_btn = QPushButton("Salvar configuracao")
        save_btn.clicked.connect(self.save_config)
        layout.addSpacing(4)
        layout.addWidget(save_btn)
        layout.addStretch(1)
        scroll.setWidget(container)
        outer_layout.addWidget(scroll)

    def load(self, app_config: AppConfig, op_config: Optional[OperationalConfig]) -> None:
        self.storage_label.setText(app_config.storage_root)
        self.fleet_table.setRowCount(0)
        self.gangway_table.setRowCount(0)
        self.conves_table.setRowCount(0)
        self.extrato_origins_table.setRowCount(0)
        self._tide_table = TideTableConfig()
        self.tide_limit_edit.setText("0,6")
        if not op_config:
            self._refresh_tide_summary()
            return
        for vessel in op_config.frota:
            self.add_fleet_row(vessel)
        for item in op_config.gangway:
            self.add_gangway_row(item)
        for item in op_config.embarcacoes_conves:
            self.add_conves_row(item)
        for item in op_config.origens_extrato:
            self.add_extrato_origin_row(item)
        self._tide_table = op_config.tabua_mare
        self.tide_limit_edit.setText(self._format_decimal(op_config.mare_limite_m))
        self._refresh_tide_summary()

    def add_fleet_row(self, vessel: Optional[FleetVessel] = None) -> None:
        row = self.fleet_table.rowCount()
        self.fleet_table.insertRow(row)
        values = [
            vessel.nome if vessel else "",
            vessel.tipo if vessel else "surfer",
            str(vessel.capacidade if vessel else 24),
            str(vessel.velocidade if vessel else 14.0),
            self._format_decimal(vessel.tempo_aproximacao_min if vessel else 0.0),
            self._format_decimal(vessel.tempo_travessia_pax_min if vessel else 1.0),
            "SIM" if vessel is None or vessel.ativa else "NAO",
        ]
        for col, value in enumerate(values):
            self.fleet_table.setItem(row, col, QTableWidgetItem(value))

    def add_gangway_row(self, value: str = "") -> None:
        row = self.gangway_table.rowCount()
        self.gangway_table.insertRow(row)
        self.gangway_table.setItem(row, 0, QTableWidgetItem(value))

    def add_conves_row(self, value: str = "") -> None:
        row = self.conves_table.rowCount()
        self.conves_table.insertRow(row)
        self.conves_table.setItem(row, 0, QTableWidgetItem(value))

    def add_extrato_origin_row(self, origin: Optional[ExtratoOriginConfig] = None) -> None:
        row = self.extrato_origins_table.rowCount()
        self.extrato_origins_table.insertRow(row)
        values = [
            origin.codigo if origin else "",
            "SIM" if origin is None or origin.ativa else "NAO",
            ", ".join(origin.aceitar) if origin else "",
            ", ".join(origin.descartar) if origin else "",
        ]
        for col, value in enumerate(values):
            self.extrato_origins_table.setItem(row, col, QTableWidgetItem(str(value)))

    @staticmethod
    def _split_aliases(value: str) -> List[str]:
        raw = (value or "").replace("\n", ",").replace(";", ",")
        return [item.strip() for item in raw.split(",") if item.strip()]

    @staticmethod
    def _format_decimal(value: float) -> str:
        text = f"{float(value or 0.0):.2f}".rstrip("0").rstrip(".")
        return text.replace(".", ",")

    @staticmethod
    def _parse_decimal(value: str) -> float:
        text = (value or "").strip().replace(",", ".")
        return float(text or "0")

    def _set_tide_status(self, text: str, level: str = "info") -> None:
        colors = {
            "ok": "#166534",
            "warning": "#b45309",
            "info": "#334155",
        }
        self.tide_status_label.setText(text)
        self.tide_status_label.setStyleSheet(f"color: {colors.get(level, '#334155')};")

    def _refresh_tide_summary(self) -> None:
        if not self._tide_table.has_data():
            self.tide_source_label.setText("Nenhuma tabua carregada")
            self.tide_year_label.setText("-")
            self.tide_days_label.setText("0")
            self._set_tide_status(
                "Selecione o PDF anual da tabua para habilitar a consulta por dia na aba Programacao.",
                "warning",
            )
            return
        day_count = len(self._tide_table.dias)
        source_name = self._tide_table.arquivo_origem or "Arquivo sem nome"
        year_text = str(self._tide_table.ano_referencia or "-")
        self.tide_source_label.setText(source_name)
        self.tide_year_label.setText(year_text)
        self.tide_days_label.setText(str(day_count))
        current_year = date.today().year
        if self._tide_table.ano_referencia and self._tide_table.ano_referencia != current_year:
            self._set_tide_status(
                f"A tabua carregada e de {self._tide_table.ano_referencia}, mas o ano atual e {current_year}. "
                "Atualize a tabua quando o ciclo operacional mudar de ano.",
                "warning",
            )
            return
        if day_count not in (365, 366):
            self._set_tide_status(
                f"A tabua foi importada, mas trouxe {day_count} dias. Revise o PDF para confirmar se a leitura ficou completa.",
                "warning",
            )
            return
        self._set_tide_status("Tabua carregada e pronta para consulta na aba Programacao.", "ok")

    def select_tide_pdf(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar PDF da Tabua de Mare",
            filter="PDF (*.pdf)",
        )
        if not file_name:
            return
        try:
            self._tide_table = self.service.import_tide_table_pdf(Path(file_name))
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Tabua de Mare",
                f"Nao foi possivel importar o PDF da tabua:\n{exc}",
            )
            return
        self._refresh_tide_summary()
        QMessageBox.information(
            self,
            "Tabua de Mare",
            f"Tabua importada com sucesso.\nAno reconhecido: {self._tide_table.ano_referencia}\n"
            f"Dias carregados: {len(self._tide_table.dias)}",
        )

    def save_config(self) -> None:
        root = self.parent_window.current_root
        try:
            mare_limite = self._parse_decimal(self.tide_limit_edit.text())
        except ValueError:
            QMessageBox.warning(
                self,
                "Configuracao",
                "Altura limite de mare invalida. Use numero como 0,6.",
            )
            return
        vessels: List[FleetVessel] = []
        try:
            for row in range(self.fleet_table.rowCount()):
                nome = self._text(self.fleet_table, row, 0)
                if not nome:
                    continue
                vessels.append(
                    FleetVessel(
                        nome=nome,
                        tipo=self._text(self.fleet_table, row, 1) or "surfer",
                        capacidade=int(self._text(self.fleet_table, row, 2) or 24),
                        velocidade=self._parse_decimal(self._text(self.fleet_table, row, 3) or "14"),
                        tempo_aproximacao_min=self._parse_decimal(
                            self._text(self.fleet_table, row, 4) or "0"
                        ),
                        tempo_travessia_pax_min=self._parse_decimal(
                            self._text(self.fleet_table, row, 5) or "1"
                        ),
                        ativa=(self._text(self.fleet_table, row, 6).upper() == "SIM"),
                    )
                )
        except ValueError:
            QMessageBox.warning(
                self,
                "Configuracao",
                "Revise os numeros da frota. Use valores como 24, 14, 25 ou 1,5.",
            )
            return
        gangway = [
            self._text(self.gangway_table, row, 0)
            for row in range(self.gangway_table.rowCount())
            if self._text(self.gangway_table, row, 0)
        ]
        embarcacoes_conves = [
            self._text(self.conves_table, row, 0)
            for row in range(self.conves_table.rowCount())
            if self._text(self.conves_table, row, 0)
        ]
        origens_extrato: List[ExtratoOriginConfig] = []
        for row in range(self.extrato_origins_table.rowCount()):
            codigo = self._text(self.extrato_origins_table, row, 0).upper()
            if not codigo:
                continue
            origens_extrato.append(
                ExtratoOriginConfig(
                    codigo=codigo,
                    ativa=self._text(self.extrato_origins_table, row, 1).upper() != "NAO",
                    aceitar=self._split_aliases(self._text(self.extrato_origins_table, row, 2)),
                    descartar=self._split_aliases(self._text(self.extrato_origins_table, row, 3)),
                )
            )
        self.service.save_operational_config(
            root,
            OperationalConfig(
                frota=vessels,
                unidades=self.parent_window.current_op_config.unidades if self.parent_window.current_op_config else [],
                gangway=gangway,
                embarcacoes_conves=embarcacoes_conves,
                origens_extrato=origens_extrato,
                mare_limite_m=mare_limite,
                tabua_mare=self._tide_table,
            ),
        )
        self.parent_window.reload_config()
        QMessageBox.information(self, "Configuracao", "Configuracao salva.")

    @staticmethod
    def _text(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item else ""

    @staticmethod
    def remove_selected_rows(table: QTableWidget) -> None:
        selected_rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        if not selected_rows and table.currentRow() >= 0:
            selected_rows = [table.currentRow()]
        for row in selected_rows:
            table.removeRow(row)


class VersionEditor(QWidget):
    def __init__(self, service: AppService, parent_window: "MainWindow", version_name: str):
        super().__init__()
        self.service = service
        self.parent_window = parent_window
        self.version_name = version_name
        self.user_edit = QLineEdit()
        self.boats_table = AutoAppendTableWidget(0, 3)
        self.demand_table = AutoAppendTableWidget(0, 5)
        self._extra_origin_columns: List[str] = ["M1"]
        self.output_text = QTextEdit()
        self.manual_route_text = QTextEdit()
        self.export_program_button = QPushButton("Exportar planilha")
        self.export_cl_txt_button = QPushButton("Exportar TXT de distribuicao")
        self.compare_routes_button = QPushButton("Comparar roteiros")
        self.tide_status_label = QLabel()
        self.tide_source_label = QLabel()
        self.tide_day_combo = QComboBox()
        self.tide_operation_day_button = QPushButton("Usar data da operacao")
        self.tide_values_table = QTableWidget(0, 2)
        self._current_tide_table = TideTableConfig()
        self.imported_csv_path: Optional[Path] = None
        self._solver_thread: Optional[QThread] = None
        self._solver_worker: Optional[SolverRunWorker] = None
        self._solver_dialog: Optional[SolverProgressDialog] = None
        self._pending_tide_rerun = False
        self._last_applied_tide_signature: Optional[str] = None
        self._build()

    @staticmethod
    def _set_compact_grid_button(button: QPushButton) -> None:
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        button.setMinimumSize(0, 0)
        button.setStyleSheet("padding: 0px 8px; margin: 0px; min-width: 0px; min-height: 0px;")

    def _version_file_slug(self) -> str:
        if self.version_name == VERSION_CL:
            return "programacao"
        return self.version_name

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setMinimumHeight(0)
        content_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        content_widget = QWidget()
        content_widget.setMinimumHeight(0)
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        # --- Header Section (Metadata) ---
        header_group = QGroupBox("Dados da Operacao")
        header_layout = QHBoxLayout(header_group)
        self.user_edit.setFixedWidth(220)
        header_layout.addWidget(QLabel("Usuario:"))
        header_layout.addWidget(self.user_edit)
        header_layout.addStretch(1)
        content_layout.addWidget(header_group)

        tide_group = QGroupBox("Tabua de Mare")
        tide_layout = QVBoxLayout(tide_group)
        self.tide_status_label.setWordWrap(True)
        self.tide_source_label.setWordWrap(True)
        tide_layout.addWidget(self.tide_status_label)
        tide_form = QFormLayout()
        tide_form.addRow("Arquivo", self.tide_source_label)
        tide_controls = QHBoxLayout()
        self.tide_day_combo.currentIndexChanged.connect(self._on_tide_day_changed)
        tide_controls.addWidget(self.tide_day_combo, 1)
        self.tide_operation_day_button.clicked.connect(self._select_operation_day)
        tide_controls.addWidget(self.tide_operation_day_button, 0)
        tide_controls_widget = QWidget()
        tide_controls_widget.setLayout(tide_controls)
        tide_form.addRow("Dia", tide_controls_widget)
        tide_layout.addLayout(tide_form)
        self.tide_values_table.setHorizontalHeaderLabels(["Hora", "Altura (m)"])
        self.tide_values_table.verticalHeader().setVisible(False)
        self.tide_values_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tide_values_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tide_values_table.setMinimumHeight(150)
        if hasattr(self.tide_values_table, "setEditTriggers"):
            self.tide_values_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        if hasattr(self.tide_values_table, "setSelectionMode"):
            self.tide_values_table.setSelectionMode(QAbstractItemView.NoSelection)
        tide_layout.addWidget(self.tide_values_table)
        content_layout.addWidget(tide_group)

        # --- Main Splitter (Vertical: Inputs vs Output) ---
        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setChildrenCollapsible(USE_COLLAPSIBLE_SPLITTERS)
        main_splitter.setMinimumHeight(0)
        main_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # --- Input Section (Splitter Horizontal) ---
        input_splitter = QSplitter(Qt.Horizontal)
        input_splitter.setChildrenCollapsible(USE_COLLAPSIBLE_SPLITTERS)
        input_splitter.setMinimumHeight(0)
        input_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Left: Boats
        boats_box = QGroupBox("Embarcacoes disponiveis")
        boats_box.setMinimumHeight(0)
        boats_layout = QVBoxLayout(boats_box)
        boats_layout.setSpacing(BUTTON_GRID_SPACING)
        route_hint_section = CollapsibleSection("Como digitar a rota", expanded=False)
        route_hint = QLabel(ROUTE_HELP_TEXT)
        route_hint.setWordWrap(True)
        route_hint.setStyleSheet("color: #475569; font-size: 11px;")
        route_hint_section.add_widget(route_hint)
        boats_layout.addWidget(route_hint_section)
        self.boats_table.set_block_delete_backspace(True)
        self.boats_table.setColumnCount(3)
        self.boats_table.setHorizontalHeaderLabels(["Nome", "Hora saida", "Rota fixa"])
        self.boats_table.setMinimumHeight(0)
        boats_header = self.boats_table.horizontalHeader()
        boats_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        boats_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        boats_header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.boats_table.itemChanged.connect(self._on_boats_item_changed)
        boats_layout.addWidget(self.boats_table)

        boats_btns = QHBoxLayout()
        boats_btns.setSpacing(BUTTON_GRID_SPACING)
        add_boat = QPushButton("Add barco")
        add_boat.clicked.connect(self.add_boat_row)
        remove_boat = QPushButton("Exc. barco")
        remove_boat.clicked.connect(lambda: self.remove_selected_rows(self.boats_table))
        build_route = QPushButton("Montar rota")
        build_route.clicked.connect(self._open_route_builder_for_selected_row)
        saved_routes_btn = QPushButton("Rotas salvas")
        saved_routes_btn.clicked.connect(self._open_saved_routes_for_selected_row)
        self._set_compact_grid_button(add_boat)
        self._set_compact_grid_button(remove_boat)
        self._set_compact_grid_button(build_route)
        self._set_compact_grid_button(saved_routes_btn)
        boats_btns.addWidget(add_boat)
        boats_btns.addWidget(remove_boat)
        boats_btns.addWidget(build_route)
        boats_btns.addWidget(saved_routes_btn)
        boats_btns.addStretch(1)
        boats_layout.addLayout(boats_btns)
        boats_layout.setStretch(0, 0)  # hint
        boats_layout.setStretch(1, 1)  # tabela recebe expansao vertical
        boats_layout.setStretch(2, 0)  # barra de acoes fixa
        input_splitter.addWidget(boats_box)

        # Right: Demand
        demand_box = QGroupBox("Demanda")
        demand_box.setMinimumHeight(0)
        demand_layout = QVBoxLayout(demand_box)
        demand_layout.setSpacing(BUTTON_GRID_SPACING)
        demand_hint_section = CollapsibleSection("Dica de preenchimento da demanda", expanded=False)
        demand_hint = QLabel(DEMAND_HELP_TEXT)
        demand_hint.setWordWrap(True)
        demand_hint.setStyleSheet("color: #475569; font-size: 11px;")
        demand_hint_section.add_widget(demand_hint)
        demand_layout.addWidget(demand_hint_section)
        self.demand_table.set_append_row_callback(self.add_demand_row)
        self.demand_table.set_block_delete_backspace(True)
        self._configure_demand_table_columns(None)
        self.demand_table.setMinimumHeight(0)
        demand_layout.addWidget(self.demand_table)

        demand_btns = QHBoxLayout()
        demand_btns.setSpacing(BUTTON_GRID_SPACING)
        add_demand = QPushButton("Add linha")
        add_demand.clicked.connect(self.add_demand_row)
        remove_demand = QPushButton("Exc. linha")
        remove_demand.clicked.connect(lambda: self.remove_selected_rows(self.demand_table))
        import_csv = QPushButton("Imp. csv")
        import_csv.clicked.connect(self.import_csv)
        import_pdf = QPushButton("Imp. Extrato pdf")
        import_pdf.clicked.connect(self.import_extrato_pdf)
        export_csv = QPushButton("Exp. csv")
        export_csv.clicked.connect(self.export_csv)
        self._set_compact_grid_button(add_demand)
        self._set_compact_grid_button(remove_demand)
        self._set_compact_grid_button(import_csv)
        self._set_compact_grid_button(import_pdf)
        self._set_compact_grid_button(export_csv)
        demand_btns.addWidget(add_demand)
        demand_btns.addWidget(remove_demand)
        demand_btns.addWidget(import_csv)
        demand_btns.addWidget(import_pdf)
        demand_btns.addWidget(export_csv)
        demand_btns.addStretch(1)
        demand_layout.addLayout(demand_btns)
        demand_layout.setStretch(0, 0)  # hint
        demand_layout.setStretch(1, 1)  # tabela recebe expansao vertical
        demand_layout.setStretch(2, 0)  # barra de acoes fixa
        input_splitter.addWidget(demand_box)

        main_splitter.addWidget(input_splitter)

        # --- Output Section ---
        output_group = QGroupBox("Resultado / Distribuicao")
        output_group.setMinimumHeight(0)
        output_layout = QVBoxLayout(output_group)
        output_splitter = QSplitter(Qt.Horizontal)
        output_splitter.setChildrenCollapsible(USE_COLLAPSIBLE_SPLITTERS)
        output_splitter.setMinimumHeight(0)
        output_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        automatic_group = QGroupBox("Roteiro Automatico")
        automatic_layout = QVBoxLayout(automatic_group)
        self.output_text.setReadOnly(True)
        self.output_text.setMinimumHeight(0)
        automatic_layout.addWidget(self.output_text)
        output_splitter.addWidget(automatic_group)
        if self.version_name == VERSION_CL:
            manual_group = QGroupBox("Roteiro Manual")
            manual_layout = QVBoxLayout(manual_group)
            self.manual_route_text.setMinimumHeight(0)
            manual_layout.addWidget(self.manual_route_text)
            self.compare_routes_button.clicked.connect(self.compare_routes)
            manual_layout.addWidget(self.compare_routes_button)
            output_splitter.addWidget(manual_group)
            output_splitter.setStretchFactor(0, 1)
            output_splitter.setStretchFactor(1, 1)
        output_layout.addWidget(output_splitter)
        main_splitter.addWidget(output_group)

        main_splitter.setStretchFactor(0, 2)  # Inputs take more space initially
        main_splitter.setStretchFactor(1, 1)
        content_layout.addWidget(main_splitter, 1)
        content_layout.setStretch(0, 0)  # Header fixo
        content_layout.setStretch(1, 0)  # painel de mare
        content_layout.setStretch(2, 1)  # Splitter ocupa o espaco restante
        content_scroll.setWidget(content_widget)
        layout.addWidget(content_scroll, 1)

        # --- Action Buttons (Bottom) ---
        action_container = QWidget()
        action_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_row = QGridLayout(action_container)
        action_row.setHorizontalSpacing(BUTTON_GRID_SPACING)
        action_row.setVerticalSpacing(BUTTON_GRID_SPACING)
        save_btn = QPushButton("Salvar")
        save_btn.clicked.connect(self.save_only)
        run_btn = QPushButton("Gerar distribuicao")
        run_btn.clicked.connect(self.run_solver)
        save_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        run_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_row.addWidget(save_btn, 0, 0)
        action_row.addWidget(run_btn, 0, 1)
        self.export_program_button.clicked.connect(self.export_program_sheet)
        self.export_program_button.setEnabled(False)
        self.export_program_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_row.addWidget(self.export_program_button, 1, 0)
        self.export_cl_txt_button.clicked.connect(self.export_cl_distribution_txt)
        self.export_cl_txt_button.setEnabled(False)
        self.export_cl_txt_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        action_row.addWidget(self.export_cl_txt_button, 1, 1)
        layout.addWidget(action_container, 0)
        layout.setStretch(0, 1)
        layout.setStretch(1, 0)

    def reset_for_operation(self, default_user: str, op_config: Optional[OperationalConfig]) -> None:
        self.user_edit.setText("")
        self.boats_table.setRowCount(0)
        self._configure_demand_table_columns(op_config)
        self.demand_table.setRowCount(0)
        self.output_text.clear()
        self.manual_route_text.clear()
        self.export_program_button.setEnabled(False)
        self.export_cl_txt_button.setEnabled(False)
        self.imported_csv_path = None
        self._refresh_tide_panel(op_config)
        if op_config:
            for vessel in op_config.frota:
                if vessel.ativa:
                    self.add_boat_row(
                        AvailableBoat(
                            nome=vessel.nome,
                            hora_saida="",
                            rota_fixa="",
                            disponivel=True,
                        )
                    )

    def load_bundle(self, bundle: Optional[VersionBundle], default_user: str, op_config: Optional[OperationalConfig]) -> None:
        self.reset_for_operation(default_user, op_config)
        if not bundle:
            return
        version = bundle.version
        if (
            bundle.imported_csv_name
            and self.parent_window.current_root
            and self.parent_window.current_operation
        ):
            storage = self.service.network_storage(self.parent_window.current_root)
            self.imported_csv_path = (
                storage.operation_dir(
                    self.parent_window.current_operation.operacao_id,
                    self.parent_window.current_operation.data_operacao,
                )
                / version.versao
                / bundle.imported_csv_name
            )
        self.user_edit.setText(version.usuario)
        self.boats_table.setRowCount(0)
        self.demand_table.setRowCount(0)
        for boat in version.embarcacoes_disponiveis:
            self.add_boat_row(boat)
        for demand in version.demanda:
            self.add_demand_row(demand)
        self.output_text.setPlainText(bundle.distribution_text)
        self.export_program_button.setEnabled(bool(bundle.distribution_text.strip()))
        self.export_cl_txt_button.setEnabled(bool(bundle.distribution_text.strip()))

    def _set_tide_panel_status(self, text: str, level: str = "info") -> None:
        colors = {
            "ok": "#166534",
            "warning": "#b45309",
            "info": "#334155",
        }
        self.tide_status_label.setText(text)
        self.tide_status_label.setStyleSheet(f"color: {colors.get(level, '#334155')};")

    def _operation_date_for_tide(self) -> str:
        if self.parent_window.current_operation:
            return self.parent_window.current_operation.data_operacao
        return ""

    @staticmethod
    def _format_tide_height(value: float) -> str:
        return f"{float(value):.2f}".replace(".", ",")

    def _refresh_tide_panel(self, op_config: Optional[OperationalConfig]) -> None:
        self._current_tide_table = op_config.tabua_mare if op_config else TideTableConfig()
        self.tide_day_combo.blockSignals(True)
        self.tide_day_combo.clear()
        for day_iso in self._current_tide_table.available_dates():
            self.tide_day_combo.addItem(day_iso, day_iso)
        self.tide_day_combo.blockSignals(False)
        self.tide_values_table.setRowCount(0)
        self.tide_operation_day_button.setEnabled(self.tide_day_combo.count() > 0)

        if not self._current_tide_table.has_data():
            self.tide_source_label.setText("Nenhuma tabua carregada")
            self.tide_day_combo.setEnabled(False)
            self._set_tide_panel_status(
                "Nenhuma tabua de mare foi carregada. Importe o PDF anual em Configuracoes para consultar os valores por dia.",
                "warning",
            )
            return

        self.tide_day_combo.setEnabled(True)
        source_name = self._current_tide_table.arquivo_origem or "Arquivo sem nome"
        day_count = len(self._current_tide_table.dias)
        self.tide_source_label.setText(
            f"{source_name} | Ano {self._current_tide_table.ano_referencia} | {day_count} dias"
        )

        operation_date = self._operation_date_for_tide()
        if (
            operation_date
            and self._current_tide_table.ano_referencia
            and operation_date[:4].isdigit()
            and int(operation_date[:4]) != self._current_tide_table.ano_referencia
        ):
            self._set_tide_panel_status(
                f"A operacao selecionada e de {operation_date[:4]}, mas a tabua carregada e de "
                f"{self._current_tide_table.ano_referencia}. Atualize a tabua em Configuracoes.",
                "warning",
            )
        else:
            self._set_tide_panel_status(
                "Selecione um dia para consultar os horarios e alturas da tabua de mare.",
                "ok",
            )

        if operation_date and self._current_tide_table.get_day(operation_date):
            index = self.tide_day_combo.findData(operation_date)
            if index >= 0:
                self.tide_day_combo.setCurrentIndex(index)
                self._render_tide_day(operation_date)
                return
        if self.tide_day_combo.count() > 0:
            self.tide_day_combo.setCurrentIndex(0)
            self._render_tide_day(self.tide_day_combo.currentData())

    def _render_tide_day(self, day_iso: str) -> None:
        tide_day = self._current_tide_table.get_day(day_iso) if day_iso else None
        self.tide_values_table.setRowCount(0)
        if tide_day is None:
            return
        for row, event in enumerate(tide_day.eventos):
            self.tide_values_table.insertRow(row)
            hour_item = QTableWidgetItem(event.hora)
            hour_item.setTextAlignment(Qt.AlignCenter)
            height_item = QTableWidgetItem(self._format_tide_height(event.altura_m))
            height_item.setTextAlignment(Qt.AlignCenter)
            self.tide_values_table.setItem(row, 0, hour_item)
            self.tide_values_table.setItem(row, 1, height_item)
        self.tide_values_table.resizeColumnToContents(0)

    def _on_tide_day_changed(self, *_args) -> None:
        self._render_tide_day(self.tide_day_combo.currentData())

    def _select_operation_day(self) -> None:
        operation_date = self._operation_date_for_tide()
        if not operation_date:
            QMessageBox.information(
                self,
                "Tabua de Mare",
                "Selecione uma operacao para usar a data operacional na consulta da mare.",
            )
            return
        index = self.tide_day_combo.findData(operation_date)
        if index < 0:
            QMessageBox.warning(
                self,
                "Tabua de Mare",
                f"Nao ha valores carregados para {operation_date}. "
                "Verifique se a tabua do ano correto foi importada em Configuracoes.",
            )
            return
        self.tide_day_combo.setCurrentIndex(index)

    def add_boat_row(self, boat: Optional[AvailableBoat] = None) -> None:
        row = self.boats_table.rowCount()
        boat_name = boat.nome if boat else self._select_vessel_name()
        if boat is None and not boat_name:
            return
        self.boats_table.insertRow(row)
        self.boats_table.setItem(row, 0, QTableWidgetItem(boat_name))
        hour_item = QTableWidgetItem(boat.hora_saida if boat else "")
        hour_item.setTextAlignment(Qt.AlignCenter)
        self.boats_table.setItem(row, 1, hour_item)
        self.boats_table.setItem(row, 2, QTableWidgetItem(boat.rota_fixa if boat else ""))
        self._adjust_boats_columns()

    def _on_boats_item_changed(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        if item.column() == 1:
            item.setTextAlignment(Qt.AlignCenter)
        if item.column() in (0, 1):
            self._adjust_boats_columns()

    def _adjust_boats_columns(self) -> None:
        self.boats_table.resizeColumnToContents(0)
        self.boats_table.resizeColumnToContents(1)

    def _open_route_builder_for_selected_row(self) -> None:
        row = self.boats_table.currentRow()
        if row < 0:
            QMessageBox.warning(
                self,
                "Montador de rota",
                "Selecione uma linha de embarcacao para montar a rota.",
            )
            return
        self._open_route_builder_for_row(row)

    def _open_route_builder_for_row(self, row: int) -> None:
        if row < 0:
            return
        boat_name = self._text(self.boats_table, row, 0) or "Embarcacao"
        current_route = self._text(self.boats_table, row, 2)
        guided_route = self._run_guided_route_builder(boat_name=boat_name, current_route=current_route)
        if guided_route is None:
            return
        self.boats_table.setItem(row, 2, QTableWidgetItem(guided_route))

    def _open_saved_routes_for_selected_row(self) -> None:
        row = self.boats_table.currentRow()
        if row < 0:
            QMessageBox.warning(
                self,
                "Rotas salvas",
                "Selecione uma linha de embarcacao.",
            )
            return
        if not self.parent_window.current_root:
            QMessageBox.warning(
                self,
                "Rotas salvas",
                "Pasta compartilhada indisponivel.",
            )
            return
        current_route = self._text(self.boats_table, row, 2)
        dialog = SavedRoutesDialog(
            service=self.service,
            root=self.parent_window.current_root,
            current_route=current_route,
            parent=self,
        )
        if dialog.exec() == QDialog.Accepted and dialog.selected_route is not None:
            self.boats_table.setItem(row, 2, QTableWidgetItem(dialog.selected_route))

    def _run_guided_route_builder(self, boat_name: str, current_route: str) -> Optional[str]:
        if current_route.strip():
            replace = QMessageBox.question(
                self,
                "Montador de rota",
                (
                    f"A embarcacao {boat_name} ja possui uma rota fixa.\n\n"
                    "Deseja substituir pela rota montada no assistente guiado?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if replace != QMessageBox.Yes:
                return None

        QMessageBox.information(
            self,
            "Montador de rota",
            (
                "Assistente guiado iniciado.\n\n"
                "Voce respondera uma pergunta por vez.\n"
                "Para encerrar a rota, use a opcao [DESTINO FINAL] na pergunta de proxima parada."
            ),
        )

        stops: List[dict] = []
        active_batches: List[dict] = []

        start_platform = self._prompt_platform(
            title=f"Montador de rota - {boat_name}",
            label="Digite o ponto inicial:",
            allow_finish=False,
        )
        if start_platform is None:
            return None

        first_stop = self._new_route_stop(start_platform)

        while True:
            first_pickup = self._prompt_int(
                title=f"Montador de rota - {boat_name}",
                label=f"O barco vai pegar quantos pax em {start_platform}?",
                minimum=0,
                maximum=999,
                default=0,
            )
            if first_pickup is None:
                return None
            break

        if not self._register_pickup_batch(
            stop=first_stop,
            total_qty=first_pickup,
            active_batches=active_batches,
        ):
            return None
        stops.append(first_stop)

        while True:
            next_platform = self._prompt_platform(
                title=f"Montador de rota - {boat_name}",
                label="Digite a proxima parada:",
                allow_finish=True,
                finish_label="[DESTINO FINAL]",
            )
            if next_platform is None:
                return None
            if next_platform == "[DESTINO FINAL]":
                pending = sum(int(batch.get("remaining", 0)) for batch in active_batches)
                if pending > 0:
                    QMessageBox.warning(
                        self,
                        "Montador de rota",
                        (
                            f"Ainda existem {pending} pax sem desembarque registrado.\n"
                            "Informe os proximos destinos ate zerar os pax a bordo."
                        ),
                    )
                    continue
                break

            stop = self._new_route_stop(next_platform)

            if not self._collect_dropoffs_for_stop(
                stop=stop,
                active_batches=active_batches,
                boat_name=boat_name,
            ):
                return None

            pickup = self._prompt_int(
                title=f"Montador de rota - {boat_name}",
                label=f"O barco vai pegar quantos pax em {next_platform}?",
                minimum=0,
                maximum=999,
                default=0,
            )
            if pickup is None:
                return None
            if not self._register_pickup_batch(
                stop=stop,
                total_qty=pickup,
                active_batches=active_batches,
            ):
                return None

            stops.append(stop)

        route_text = self._build_route_from_stops(stops)
        if not route_text:
            QMessageBox.warning(
                self,
                "Montador de rota",
                "Nenhuma etapa valida foi preenchida. A rota nao foi alterada.",
            )
            return None

        confirm = QMessageBox.question(
            self,
            "Confirmar rota",
            f"Aplicar a rota abaixo?\n\n{route_text}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return None
        return route_text

    def _register_pickup_batch(self, stop: dict, total_qty: int, active_batches: List[dict]) -> bool:
        if total_qty <= 0:
            return True
        platform = stop["platform"]
        destinations = self._prompt_destinations_list(platform=platform, total_qty=total_qty)
        if destinations is None:
            return False
        if platform in ("TMIB", "M9"):
            stop["pickup"] = stop.get("pickup", 0) + total_qty
        active_batches.append(
            {
                "origin": platform,
                "remaining": total_qty,
                "destinations": set(destinations),
                "origin_stop": stop,
            }
        )
        return True

    def _prompt_destinations_list(self, platform: str, total_qty: int) -> Optional[List[str]]:
        while True:
            raw_destinations, ok = QInputDialog.getText(
                self,
                "Montador de rota",
                (
                    f"Digite o(s) destino(s) para os {total_qty} pax embarcados em {platform}.\n"
                    "Se houver mais de um, separe por virgulas."
                ),
            )
            if not ok:
                return None
            destinations = [part.strip().upper() for part in (raw_destinations or "").split(",") if part.strip()]
            if not destinations:
                QMessageBox.warning(self, "Montador de rota", "Informe pelo menos um destino.")
                continue
            if any(dest == platform for dest in destinations):
                QMessageBox.warning(
                    self,
                    "Montador de rota",
                    "Um destino nao pode ser igual a plataforma de origem.",
                )
                continue
            unique_destinations: List[str] = []
            seen = set()
            for destination in destinations:
                if destination in seen:
                    continue
                seen.add(destination)
                unique_destinations.append(destination)
            return unique_destinations

    def _collect_dropoffs_for_stop(self, stop: dict, active_batches: List[dict], boat_name: str) -> bool:
        platform = stop["platform"]
        candidates = [
            batch
            for batch in active_batches
            if int(batch.get("remaining", 0)) > 0 and platform in batch.get("destinations", set())
        ]
        if not candidates:
            return True

        for batch in candidates:
            remaining = int(batch.get("remaining", 0))
            if remaining <= 0:
                continue
            destinations = batch.get("destinations", set())
            if len(destinations) == 1:
                drop_qty = remaining
            else:
                if len(candidates) == 1:
                    label = f"Quantos pax vao descer em {platform}? (a bordo para este destino: {remaining})"
                else:
                    label = (
                        f"Quantos pax com origem em {batch['origin']} vao descer em {platform}? "
                        f"(a bordo para este destino: {remaining})"
                    )
                drop_qty = self._prompt_int(
                    title=f"Montador de rota - {boat_name}",
                    label=label,
                    minimum=0,
                    maximum=remaining,
                    default=0,
                )
                if drop_qty is None:
                    return False
            self._apply_dropoff(stop=stop, batch=batch, destination=platform, qty=drop_qty)

        return True

    def _apply_dropoff(self, stop: dict, batch: dict, destination: str, qty: int) -> None:
        if qty <= 0:
            return
        origin = str(batch.get("origin", "")).upper()
        if origin == "TMIB":
            stop["drop_tmib"] = int(stop.get("drop_tmib", 0)) + qty
        elif origin == "M9":
            stop["drop_m9"] = int(stop.get("drop_m9", 0)) + qty
        else:
            stop["minus_transfers"][origin] = int(stop["minus_transfers"].get(origin, 0)) + qty
            origin_stop = batch.get("origin_stop")
            if isinstance(origin_stop, dict):
                origin_stop["plus_transfers"][destination] = int(origin_stop["plus_transfers"].get(destination, 0)) + qty
        batch["remaining"] = int(batch.get("remaining", 0)) - qty

    def _new_route_stop(self, platform: str) -> dict:
        return {
            "platform": platform,
            "pickup": 0,
            "drop_tmib": 0,
            "drop_m9": 0,
            "plus_transfers": {},
            "minus_transfers": {},
        }

    def _build_route_from_stops(self, stops: List[dict]) -> str:
        parts: List[str] = []
        for stop in stops:
            platform = stop["platform"].strip().upper()
            if not platform:
                continue
            tokens: List[str] = [platform]
            if int(stop.get("pickup", 0)) > 0:
                tokens.append(f"+{int(stop['pickup'])}")
            if int(stop.get("drop_tmib", 0)) > 0:
                tokens.append(f"-{int(stop['drop_tmib'])}")
            if int(stop.get("drop_m9", 0)) > 0:
                tokens.append(f"-M9:{int(stop['drop_m9'])}")
            extra_pickup = sum(int(qty) for qty in stop.get("plus_transfers", {}).values() if int(qty) > 0)
            if extra_pickup > 0:
                total_pickup = int(stop.get("pickup", 0)) + extra_pickup
                tokens = [platform]
                tokens.append(f"+{total_pickup}")
                if int(stop.get("drop_tmib", 0)) > 0:
                    tokens.append(f"-{int(stop['drop_tmib'])}")
                if int(stop.get("drop_m9", 0)) > 0:
                    tokens.append(f"-M9:{int(stop['drop_m9'])}")
            for origin, qty in stop.get("minus_transfers", {}).items():
                if int(qty) > 0:
                    tokens.append(f"-{origin}:{int(qty)}")
            parts.append(" ".join(tokens))
        return "/".join(parts)

    def _platform_options(self) -> List[str]:
        options = {"TMIB", "M9"}
        if self.parent_window.current_op_config:
            for unit in self.parent_window.current_op_config.unidades:
                value = str(unit).strip().upper()
                if value:
                    options.add(value)
        for row in range(self.demand_table.rowCount()):
            value = self._text(self.demand_table, row, 0).upper()
            if value:
                options.add(value)
        ordered = sorted(options)
        preferred = [item for item in ("TMIB", "M9") if item in ordered]
        others = [item for item in ordered if item not in ("TMIB", "M9")]
        return preferred + others

    def _prompt_platform(
        self,
        title: str,
        label: str,
        allow_finish: bool,
        finish_label: str = "",
        forbid_value: str = "",
    ) -> Optional[str]:
        options = self._platform_options()
        if allow_finish and finish_label:
            options = [finish_label] + options

        while True:
            value, ok = QInputDialog.getItem(
                self,
                title,
                label,
                options,
                0,
                True,
            )
            if not ok:
                return None
            text = (value or "").strip().upper()
            if allow_finish and text == finish_label:
                return finish_label
            if not text:
                QMessageBox.warning(self, "Montador de rota", "Informe uma plataforma valida.")
                continue
            if forbid_value and text == forbid_value.upper():
                QMessageBox.warning(
                    self,
                    "Montador de rota",
                    "A plataforma de destino nao pode ser igual a origem nesta etapa.",
                )
                continue
            return text

    def _prompt_int(
        self,
        title: str,
        label: str,
        minimum: int,
        maximum: int,
        default: int,
    ) -> Optional[int]:
        value, ok = QInputDialog.getInt(
            self,
            title,
            label,
            default,
            minimum,
            maximum,
            1,
        )
        if not ok:
            return None
        return int(value)

    def add_demand_row(self, demand: Optional[DemandItem] = None) -> None:
        row = self.demand_table.rowCount()
        self.demand_table.insertRow(row)
        values = [
            demand.plataforma if demand else "",
            str(demand.tmib if demand else 0),
            str(demand.m9 if demand else 0),
        ]
        for origin in self._extra_origin_columns:
            values.append(str(demand.quantidade_origem(origin) if demand else 0))
        values.append(str(demand.prioridade if demand else 0))
        for col, value in enumerate(values):
            self.demand_table.setItem(row, col, QTableWidgetItem(value))

    def _configure_demand_table_columns(self, op_config: Optional[OperationalConfig]) -> None:
        origins: List[str] = []
        if op_config is not None:
            for item in op_config.origens_extrato:
                if not item.ativa:
                    continue
                code = (item.codigo or "").strip().upper()
                if not code or code in {"TMIB", "M9"} or code in origins:
                    continue
                origins.append(code)
        if not origins:
            origins = ["M1"]
        self._extra_origin_columns = origins
        self.demand_table.setColumnCount(4 + len(self._extra_origin_columns))
        self.demand_table.setHorizontalHeaderLabels(
            ["Plataforma", "TMIB", "M9", *self._extra_origin_columns, "Prioridade"]
        )

    def _priority_column(self) -> int:
        return 3 + len(self._extra_origin_columns)

    @staticmethod
    def remove_selected_rows(table: QTableWidget) -> None:
        selected_rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
        if not selected_rows and table.currentRow() >= 0:
            selected_rows = [table.currentRow()]
        for row in selected_rows:
            table.removeRow(row)

    def build_version(self) -> OperationVersion:
        user_name = self.user_edit.text().strip()
        if not user_name:
            raise ValueError("Informe o usuario antes de continuar.")
        boats: List[AvailableBoat] = []
        for row in range(self.boats_table.rowCount()):
            nome = self._text(self.boats_table, row, 0)
            if not nome:
                continue
            hora_saida = self._text(self.boats_table, row, 1)
            if not hora_saida:
                raise ValueError(f"Informe a hora de saida da embarcacao {nome}.")
            if not re.match(r"^\d{2}:\d{2}$", hora_saida):
                raise ValueError(f"Hora de saida invalida para {nome}. Use HH:MM.")
            boats.append(
                AvailableBoat(
                    nome=nome,
                    hora_saida=hora_saida,
                    rota_fixa=self._text(self.boats_table, row, 2),
                    disponivel=True,
                )
            )
        demands: List[DemandItem] = []
        for row in range(self.demand_table.rowCount()):
            plataforma = self._text(self.demand_table, row, 0)
            if not plataforma:
                continue
            extras = {
                origin: int(self._text(self.demand_table, row, 3 + idx) or 0)
                for idx, origin in enumerate(self._extra_origin_columns)
                if origin != "M1"
            }
            m1 = 0
            if "M1" in self._extra_origin_columns:
                m1_col = 3 + self._extra_origin_columns.index("M1")
                m1 = int(self._text(self.demand_table, row, m1_col) or 0)
            demands.append(
                DemandItem(
                    plataforma=plataforma,
                    tmib=int(self._text(self.demand_table, row, 1) or 0),
                    m9=int(self._text(self.demand_table, row, 2) or 0),
                    m1=m1,
                    origens_extras=extras,
                    prioridade=int(self._text(self.demand_table, row, self._priority_column()) or 0),
                )
            )
        return OperationVersion(
            versao=self.version_name,
            usuario=user_name,
            criado_em=default_operation_version(self.version_name, "").criado_em,
            tipo_origem="csv" if self.imported_csv_path else "formulario",
            troca_turma=False,
            rendidos_m9=0,
            embarcacoes_disponiveis=boats,
            demanda=demands,
        )

    def import_csv(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Selecionar CSV", filter="CSV (*.csv)")
        if not file_name:
            return
        self.imported_csv_path = Path(file_name)
        demands = self.service.import_csv(self.imported_csv_path)
        if not demands:
            QMessageBox.warning(
                self,
                "Importar CSV",
                "O arquivo CSV nao trouxe nenhuma demanda valida. Verifique cabecalhos e valores.",
            )
            return
        self.demand_table.setRowCount(0)
        for item in demands:
            self.add_demand_row(item)

    def import_extrato_pdf(self) -> None:
        if not self.parent_window.current_root:
            QMessageBox.warning(self, "Importar Extrato PDF", "Pasta compartilhada indisponivel.")
            return
        file_name, _ = QFileDialog.getOpenFileName(self, "Selecionar Extrato PDF", filter="PDF (*.pdf)")
        if not file_name:
            return
        try:
            demands = self.service.import_extrato_pdf(self.parent_window.current_root, Path(file_name))
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Importar Extrato PDF",
                f"Nao foi possivel ler o extrato PDF:\n{exc}",
            )
            return
        if not demands:
            QMessageBox.warning(
                self,
                "Importar Extrato PDF",
                "O extrato PDF nao trouxe nenhuma demanda valida.",
            )
            return
        self.imported_csv_path = None
        self.demand_table.setRowCount(0)
        for item in demands:
            self.add_demand_row(item)

    def export_csv(self) -> None:
        rows = []
        for row in range(self.demand_table.rowCount()):
            plataforma = self._text(self.demand_table, row, 0)
            if not plataforma:
                continue
            row_data = {
                "PLATAFORMA": plataforma,
                "TMIB": self._text(self.demand_table, row, 1) or "0",
                "M9": self._text(self.demand_table, row, 2) or "0",
                "PRIORIDADE": self._text(self.demand_table, row, self._priority_column()) or "0",
            }
            for idx, origin in enumerate(self._extra_origin_columns):
                row_data[origin] = self._text(self.demand_table, row, 3 + idx) or "0"
            rows.append(row_data)
        if not rows:
            QMessageBox.warning(
                self,
                "Exportar CSV",
                "Nao ha demanda preenchida para exportar.",
            )
            return

        version_slug = self._version_file_slug()
        if self.parent_window.current_operation:
            default_name = f"{self.parent_window.current_operation.operacao_id}_{version_slug}_demanda.csv"
        else:
            default_name = f"{version_slug}_demanda.csv"

        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar demanda em CSV",
            default_name,
            "CSV (*.csv)",
        )
        if not file_name:
            return

        output_path = Path(file_name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            fieldnames = ["PLATAFORMA", "TMIB", "M9", *self._extra_origin_columns, "PRIORIDADE"]
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
                delimiter=";",
            )
            writer.writeheader()
            writer.writerows(rows)
        QMessageBox.information(
            self,
            "Exportar CSV",
            f"Demanda exportada para:\n{output_path}",
        )

    def save_only(self) -> None:
        if not self.parent_window.current_operation:
            QMessageBox.warning(self, "Operacao", "Selecione ou crie uma operacao.")
            return
        try:
            version = self.build_version()
        except ValueError as exc:
            QMessageBox.warning(self, "Versao", str(exc))
            return
        self.parent_window.current_operation = self.service.save_version(
            self.parent_window.current_root,
            self.parent_window.current_operation,
            version,
            self.imported_csv_path,
        )
        self.parent_window.reload_operations(select_operation_id=self.parent_window.current_operation.operacao_id)
        QMessageBox.information(self, "Versao", "Versao salva.")
        self.parent_window.refresh_operation()

    @staticmethod
    def _tide_signature(analysis: TideAlertAnalysis) -> str:
        ordered = sorted((item.boat_name, item.suggested_departure) for item in analysis.suggestions)
        return "|".join(f"{boat}:{departure}" for boat, departure in ordered)

    def _start_solver_run(self, version: OperationVersion) -> None:
        self._solver_dialog = SolverProgressDialog(self)
        self._solver_dialog.append_message("Iniciando otimizacao de distribuicao...")

        self._solver_thread = QThread(self)
        self._solver_worker = SolverRunWorker(
            service=self.service,
            root=self.parent_window.current_root,
            metadata=self.parent_window.current_operation,
            version=version,
            imported_csv_path=self.imported_csv_path,
        )
        self._solver_worker.moveToThread(self._solver_thread)
        self._solver_thread.started.connect(self._solver_worker.run)
        self._solver_worker.progress.connect(self._on_solver_progress)
        self._solver_worker.finished.connect(self._on_solver_finished)
        self._solver_worker.failed.connect(self._on_solver_failed)
        self._solver_worker.done.connect(self._on_solver_done)
        self._solver_worker.done.connect(self._solver_thread.quit)
        self._solver_worker.done.connect(self._solver_worker.deleteLater)
        self._solver_thread.finished.connect(self._solver_thread.deleteLater)
        self._solver_thread.start()
        self._solver_dialog.show()

    def run_solver(self) -> None:
        if not self.parent_window.current_operation:
            QMessageBox.warning(self, "Operacao", "Selecione ou crie uma operacao.")
            return
        if self._solver_thread is not None:
            QMessageBox.information(
                self,
                "Distribuicao",
                "Ja existe um processamento em andamento.",
            )
            return
        try:
            version = self.build_version()
        except ValueError as exc:
            QMessageBox.warning(self, "Distribuicao", str(exc))
            return
        self._start_solver_run(version)

    def _on_solver_progress(self, message: str) -> None:
        if self._solver_dialog is not None:
            self._solver_dialog.append_message(message)

    def _evaluate_tide_alerts(self, distribution_text: str) -> TideAlertAnalysis:
        if not self.parent_window.current_root or not self.parent_window.current_operation:
            return TideAlertAnalysis()
        return self.service.analyze_tide_alerts(
            self.parent_window.current_root,
            self.parent_window.current_operation.data_operacao,
            distribution_text,
        )

    def _apply_tide_departure_suggestions(self, analysis: TideAlertAnalysis) -> None:
        updates = {item.boat_name: item.suggested_departure for item in analysis.suggestions}
        for row in range(self.boats_table.rowCount()):
            boat_name = self._text(self.boats_table, row, 0)
            suggested = updates.get(boat_name)
            if suggested:
                self.boats_table.setItem(row, 1, QTableWidgetItem(suggested))

    def _handle_tide_alerts(self, distribution_text: str) -> bool:
        analysis = self._evaluate_tide_alerts(distribution_text)
        if not analysis.has_items():
            self._last_applied_tide_signature = None
            return False

        dialog = TideAlertDialog(analysis, self)
        dialog.exec()
        if not dialog.apply_requested:
            self._last_applied_tide_signature = None
            return False

        signature = self._tide_signature(analysis)
        if signature and signature == self._last_applied_tide_signature:
            QMessageBox.warning(
                self,
                "Alerta de Mare",
                "O ajuste sugerido de mare permaneceu igual apos a ultima reaplicacao. Revise os horarios manualmente.",
            )
            return False

        self._apply_tide_departure_suggestions(analysis)
        self._pending_tide_rerun = True
        self._last_applied_tide_signature = signature or None
        return True

    def _on_solver_finished(self, updated_operation: OperationMetadata, result: SolverRunResult) -> None:
        self.parent_window.current_operation = updated_operation
        self.output_text.setPlainText(result.distribution_text)
        self.export_program_button.setEnabled(bool(result.distribution_text.strip()))
        self.export_cl_txt_button.setEnabled(bool(result.distribution_text.strip()))
        self.parent_window.reload_operations(select_operation_id=updated_operation.operacao_id)
        self.parent_window.refresh_operation()
        if self._solver_dialog is not None:
            self._solver_dialog.append_message("Distribuicao concluida. Validando regra de mare...")
            self._solver_dialog.finish(success=True)
            self._solver_dialog.accept()
        rerun_requested = False
        try:
            rerun_requested = self._handle_tide_alerts(result.distribution_text)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Alerta de Mare",
                f"Nao foi possivel avaliar a regra de mare baixa:\n{exc}",
            )
        if (not rerun_requested) and self.version_name in (VERSION_PROGRAMACAO, VERSION_CL):
            self._offer_program_sheet_export(result.distribution_text)

    def _on_solver_failed(self, error_message: str) -> None:
        if self._solver_dialog is not None:
            self._solver_dialog.append_message("Falha durante a geracao da distribuicao.")
            self._solver_dialog.finish(success=False)
            self._solver_dialog.accept()
        QMessageBox.warning(
            self,
            "Distribuicao",
            f"Nao foi possivel gerar a distribuicao:\n{error_message}",
        )

    def _on_solver_done(self) -> None:
        self._solver_worker = None
        self._solver_thread = None
        self._solver_dialog = None
        if self._pending_tide_rerun:
            self._pending_tide_rerun = False
            QTimer.singleShot(0, self.run_solver)

    def compare_routes(self) -> None:
        automatic_distribution = self.output_text.toPlainText().strip()
        manual_distribution = self.manual_route_text.toPlainText().strip()
        if not automatic_distribution:
            QMessageBox.warning(
                self,
                "Comparar roteiros",
                "Gere ou carregue uma distribuicao automatica antes de comparar.",
            )
            return
        if not manual_distribution:
            QMessageBox.warning(
                self,
                "Comparar roteiros",
                "Preencha o roteiro manual antes de comparar.",
            )
            return
        try:
            result = self.service.compare_automatic_vs_manual_routes(
                self.parent_window.current_root,
                automatic_distribution,
                manual_distribution,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Comparar roteiros", str(exc))
            return
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Comparar roteiros",
                f"Nao foi possivel comparar os roteiros:\n{exc}",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Comparacao de Roteiros")
        dialog.resize(900, 650)
        layout = QVBoxLayout(dialog)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setHtml(self._build_route_comparison_html(result))
        layout.addWidget(viewer)
        dialog.exec()

    @staticmethod
    def _build_route_comparison_html(result: dict) -> str:
        distance_rows = (
            "<tr>"
            "<td>Distancia total percorrida (NM)</td>"
            f"<td>{result['automatic_total_distance_nm']}</td>"
            f"<td>{result['manual_total_distance_nm']}</td>"
            "</tr>"
        )
        platform_rows = "".join(
            [
                "<tr>"
                f"<td>{row['platform']}</td>"
                f"<td>{row['automatic_arrival']}</td>"
                f"<td>{row['manual_arrival']}</td>"
                "</tr>"
                for row in result["platform_rows"]
            ]
        )
        return f"""
        <html>
        <head>
        <style>
        body {{ font-family: Segoe UI, Arial, sans-serif; color: #1f2937; margin: 10px; }}
        h2 {{ margin: 18px 0 8px; font-size: 18px; color: #0f172a; }}
        table {{ border-collapse: collapse; width: 100%; margin: 8px 0 18px; table-layout: fixed; }}
        th {{ background: #e2e8f0; color: #0f172a; font-weight: 600; text-align: left; }}
        th, td {{ border: 1px solid #cbd5e1; padding: 8px 10px; vertical-align: top; font-size: 12px; word-wrap: break-word; }}
        </style>
        </head>
        <body>
        <h2>Resumo</h2>
        <table>
        <thead>
        <tr><th>INDICADOR</th><th>AUTOMATICO</th><th>MANUAL</th></tr>
        </thead>
        <tbody>
        {distance_rows}
        </tbody>
        </table>
        <h2>Horario de chegada por plataforma</h2>
        <table>
        <thead>
        <tr><th>PLATAFORMA</th><th>AUTOMATICO</th><th>MANUAL</th></tr>
        </thead>
        <tbody>
        {platform_rows}
        </tbody>
        </table>
        </body>
        </html>
        """

    def export_program_sheet(self) -> None:
        distribution_text = self.output_text.toPlainText().strip()
        if not distribution_text:
            QMessageBox.warning(
                self,
                "Planilha de Programacao",
                "Gere ou carregue uma distribuicao antes de exportar a planilha.",
            )
            return
        self._offer_program_sheet_export(distribution_text)

    def _offer_program_sheet_export(self, distribution_text: str) -> None:
        response = QMessageBox.question(
            self,
            "Planilha de Programacao",
            "Deseja salvar a planilha de programacao gerada?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if response != QMessageBox.Yes:
            return
        default_name = self._default_program_sheet_name()
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar planilha de programacao",
            default_name,
            "Planilhas Excel (*.xlsx)",
        )
        if not file_name:
            return
        try:
            saved = self.service.export_program_sheet(
                self.parent_window.current_root,
                distribution_text,
                Path(file_name),
            )
            QMessageBox.information(
                self,
                "Planilha de Programacao",
                f"Planilha salva em:\n{saved}",
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Planilha de Programacao",
                f"Nao foi possivel gerar a planilha:\n{exc}",
            )

    def show_route_help(self) -> None:
        QMessageBox.information(
            self,
            "Como digitar a rota",
            ROUTE_HELP_TEXT,
        )

    def export_cl_distribution_txt(self) -> None:
        if not self.parent_window.current_operation:
            QMessageBox.warning(self, "Operacao", "Selecione ou crie uma operacao.")
            return
        distribution_text = self.output_text.toPlainText().strip()
        if not distribution_text:
            QMessageBox.warning(
                self,
                "TXT de Distribuicao",
                "Gere ou carregue uma distribuicao antes de exportar o TXT.",
            )
            return
        try:
            version = self.build_version()
        except ValueError as exc:
            QMessageBox.warning(self, "TXT de Distribuicao", str(exc))
            return
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar TXT de distribuicao",
            self._default_cl_distribution_txt_name(),
            "Arquivos TXT (*.txt)",
        )
        if not file_name:
            return
        try:
            saved = self.service.export_cl_distribution_txt(
                self.parent_window.current_root,
                self.parent_window.current_operation,
                version,
                distribution_text,
                Path(file_name),
            )
            QMessageBox.information(
                self,
                "TXT de Distribuicao",
                f"Arquivo salvo em:\n{saved}",
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "TXT de Distribuicao",
                f"Nao foi possivel gerar o TXT:\n{exc}",
            )

    def _default_program_sheet_name(self) -> str:
        operation = self.parent_window.current_operation
        version_slug = self._version_file_slug()
        if not operation:
            return f"{version_slug}_programacao.xlsx"
        date_token = operation.data_operacao.replace("-", "_")
        return f"{date_token}_{operation.operacao_id}_{version_slug}_programacao.xlsx"

    def _default_cl_distribution_txt_name(self) -> str:
        operation = self.parent_window.current_operation
        version_slug = self._version_file_slug()
        if not operation:
            return f"{version_slug}_distribuicao.txt"
        date_token = operation.data_operacao.replace("-", "_")
        return f"{date_token}_{operation.operacao_id}_{version_slug}_distribuicao.txt"

    def _available_vessel_names(self) -> List[str]:
        op_config = self.parent_window.current_op_config
        if not op_config:
            return []
        return [vessel.nome for vessel in op_config.frota if vessel.ativa]

    def _select_vessel_name(self) -> str:
        vessel_names = self._available_vessel_names()
        if not vessel_names:
            QMessageBox.warning(
                self,
                "Embarcacoes",
                "Nao ha embarcacoes ativas cadastradas na configuracao.",
            )
            return ""
        selected, ok = QInputDialog.getItem(
            self,
            "Selecionar embarcacao",
            "Embarcacao:",
            vessel_names,
            0,
            False,
        )
        if not ok:
            return ""
        return str(selected).strip()

    @staticmethod
    def _text(table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item else ""


class ComparisonTab(QWidget):
    def __init__(self, service: AppService, parent_window: "MainWindow"):
        super().__init__()
        self.service = service
        self.parent_window = parent_window
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_text)

    def load(self, comparison: Optional[dict]) -> None:
        if not comparison:
            self.summary_text.setHtml("<p>Comparacao indisponivel.</p>")
            return
        details = comparison.get("details", "")
        if details.lstrip().startswith("<"):
            self.summary_text.setHtml(details)
        else:
            self.summary_text.setPlainText(details)


class PickupBoatPositionsDialog(QDialog):
    def __init__(self, boat_states: List[PickupBoatState], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Alterar posicao das lanchas")
        self.resize(520, 420)
        self.table = QTableWidget(0, 2)
        self._build(boat_states)

    def _build(self, boat_states: List[PickupBoatState]) -> None:
        layout = QVBoxLayout(self)
        info = QLabel(
            "Informe a plataforma atual de cada lancha. A tela abre com a posicao inferida ao fim da entrega inicial."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #475569;")
        layout.addWidget(info)

        self.table.setHorizontalHeaderLabels(["Embarcacao", "Posicao atual"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        apply_btn = QPushButton("Aplicar")
        apply_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(apply_btn)
        layout.addLayout(buttons)

        self.table.setRowCount(0)
        for state in boat_states:
            row = self.table.rowCount()
            self.table.insertRow(row)
            boat_item = QTableWidgetItem(state.nome)
            boat_item.setFlags(boat_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, boat_item)
            self.table.setItem(row, 1, QTableWidgetItem(state.localizacao or "TMIB"))

    def positions(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for row in range(self.table.rowCount()):
            nome = VersionEditor._text(self.table, row, 0)
            if not nome:
                continue
            result[nome] = VersionEditor._text(self.table, row, 1) or "TMIB"
        return result


class PickupTab(QWidget):
    def __init__(self, service: AppService, parent_window: "MainWindow"):
        super().__init__()
        self.service = service
        self.parent_window = parent_window
        self.version_combo = QComboBox()
        self.pickup_engine_combo = QComboBox()
        self.surfer_cutoff_edit = QLineEdit("17:40")
        self.boats_table = QTableWidget(0, 4)
        self.demand_table = QTableWidget(0, 5)
        self.output_text = QTextEdit()
        self._loaded_boat_states: List[PickupBoatState] = []
        self._loaded_demands: List[PickupDemand] = []
        self._loaded_version: Optional[OperationVersion] = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        controls = QGroupBox("Parametros do Recolhimento")
        controls_layout = QGridLayout(controls)
        self.version_combo.addItem("Programacao", VERSION_CL)
        self.version_combo.setEnabled(False)
        self.pickup_engine_combo.addItem("Legado (v2 atual)", "legacy_v2")
        self.pickup_engine_combo.addItem("PD V1 (novo teste)", "pd_v1")
        load_btn = QPushButton("Carregar operacao")
        load_btn.clicked.connect(self.load_current_version)
        controls_layout.addWidget(QLabel("Base da distribuicao:"), 0, 0)
        controls_layout.addWidget(self.version_combo, 0, 1)
        controls_layout.addWidget(QLabel("Motor:"), 0, 2)
        controls_layout.addWidget(self.pickup_engine_combo, 0, 3)
        controls_layout.addWidget(QLabel("Chegada ao TMIB:"), 1, 0)
        controls_layout.addWidget(self.surfer_cutoff_edit, 1, 1)
        controls_layout.addWidget(load_btn, 1, 4)
        layout.addWidget(controls)

        info = QLabel(
            "O estado padrao considera as lanchas na posicao em que terminaram a entrega inicial. "
            "Use o botao de alteracao de posicao se elas tiverem sido usadas em outras missoes durante o dia."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #475569;")
        layout.addWidget(info)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(USE_COLLAPSIBLE_SPLITTERS)

        boats_group = QGroupBox("Estado Atual da Frota")
        boats_layout = QVBoxLayout(boats_group)
        self.boats_table.setHorizontalHeaderLabels(
            ["Disponivel", "Embarcacao", "Localizacao", "Rota fixa recolh."]
        )
        boats_header = self.boats_table.horizontalHeader()
        boats_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        boats_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        boats_header.setSectionResizeMode(2, QHeaderView.Stretch)
        boats_header.setSectionResizeMode(3, QHeaderView.Stretch)
        boats_layout.addWidget(self.boats_table)
        boats_btns = QHBoxLayout()
        edit_positions_btn = QPushButton("Alterar posicao das lanchas")
        edit_positions_btn.clicked.connect(self.edit_boat_positions)
        add_boat_btn = QPushButton("Incluir embarcacao")
        add_boat_btn.clicked.connect(self.add_boat_row)
        remove_boat_btn = QPushButton("Excluir embarcacao")
        remove_boat_btn.clicked.connect(self.remove_boat_row)
        boats_btns.addWidget(edit_positions_btn)
        boats_btns.addWidget(add_boat_btn)
        boats_btns.addWidget(remove_boat_btn)
        boats_btns.addStretch(1)
        boats_layout.addLayout(boats_btns)
        splitter.addWidget(boats_group)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        demand_group = QGroupBox("Demanda de Recolhimento")
        demand_layout = QVBoxLayout(demand_group)
        self.demand_table.setHorizontalHeaderLabels(["Plataforma", "TMIB", "M9", "M1", "Prio"])
        demand_header = self.demand_table.horizontalHeader()
        demand_header.setSectionResizeMode(0, QHeaderView.Stretch)
        demand_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        demand_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        demand_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        demand_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        demand_layout.addWidget(self.demand_table)
        demand_btns = QHBoxLayout()
        add_demand_btn = QPushButton("Incluir plataforma")
        add_demand_btn.clicked.connect(self.add_demand_row)
        remove_demand_btn = QPushButton("Excluir plataforma")
        remove_demand_btn.clicked.connect(self.remove_demand_row)
        export_demand_btn = QPushButton("Exportar CSV")
        export_demand_btn.clicked.connect(self.export_pickup_csv)
        plan_btn = QPushButton("Planejar recolhimento")
        plan_btn.clicked.connect(self.plan_pickup)
        start_now_btn = QPushButton("Iniciar recolhimento agora")
        start_now_btn.clicked.connect(self.start_pickup_now)
        demand_btns.addWidget(add_demand_btn)
        demand_btns.addWidget(remove_demand_btn)
        demand_btns.addWidget(export_demand_btn)
        demand_btns.addStretch(1)
        demand_btns.addWidget(plan_btn)
        demand_btns.addWidget(start_now_btn)
        demand_layout.addLayout(demand_btns)
        right_layout.addWidget(demand_group)
        output_group = QGroupBox("Plano Gerado")
        output_layout = QVBoxLayout(output_group)
        self.output_text.setReadOnly(True)
        output_layout.addWidget(self.output_text)
        right_layout.addWidget(output_group)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

    def clear(self, message: str = "") -> None:
        self._loaded_boat_states = []
        self._loaded_demands = []
        self._loaded_version = None
        self.boats_table.setRowCount(0)
        self.demand_table.setRowCount(0)
        self.output_text.setPlainText(message)

    def set_operation(self) -> None:
        if not self.parent_window.current_operation or not self.parent_window.current_root:
            self.clear("Selecione uma operacao para planejar o recolhimento.")
            return
        self.load_current_version()

    def load_current_version(self) -> None:
        if not self.parent_window.current_operation or not self.parent_window.current_root:
            self.clear("Selecione uma operacao para planejar o recolhimento.")
            return
        version_name = self.version_combo.currentData()
        try:
            bundle, boat_states, pickup_demands = self.service.load_pickup_context(
                self.parent_window.current_root,
                self.parent_window.current_operation,
                version_name,
            )
        except Exception as exc:
            self.clear(f"Nao foi possivel carregar o recolhimento:\n{exc}")
            return
        self._loaded_demands = [
            PickupDemand(
                plataforma=item.plataforma,
                origem=item.origem,
                quantidade=int(item.quantidade),
                prioridade=int(item.prioridade),
            )
            for item in pickup_demands
        ]
        self._populate_demand_table(self._loaded_demands)
        if bundle is None:
            self._loaded_boat_states = []
            self._loaded_version = None
            self.boats_table.setRowCount(0)
            self.output_text.setPlainText("Versao ainda nao salva.")
            return
        self._loaded_version = bundle.version
        self._loaded_boat_states = [
            PickupBoatState(
                nome=item.nome,
                localizacao=item.localizacao,
                hora_disponivel=item.hora_disponivel,
                disponivel=item.disponivel,
                viagens_maximas=item.viagens_maximas,
                rota_fixa=item.rota_fixa,
            )
            for item in boat_states
        ]
        self._populate_boat_table(boat_states)
        if not bundle.distribution_text.strip():
            self.output_text.setPlainText(
                "Versao carregada, mas ainda sem distribuicao. Gere a distribuicao antes de planejar o recolhimento."
            )
            return
        self.output_text.setPlainText(
            "Estado da frota carregado a partir da distribuicao atual.\n"
            "Rotas fixas de recolhimento programadas na Programacao foram trazidas para a tabela da frota.\n"
            "Se as lanchas foram usadas ao longo do dia, ajuste a posicao atual antes de planejar o recolhimento."
        )

    def _populate_boat_table(self, boat_states: List[PickupBoatState]) -> None:
        self.boats_table.setRowCount(0)
        for state in boat_states:
            row = self.boats_table.rowCount()
            self.boats_table.insertRow(row)
            values = [
                "SIM" if state.disponivel else "NAO",
                state.nome,
                state.localizacao,
                state.rota_fixa,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.boats_table.setItem(row, col, item)

    def _populate_demand_table(self, demands: List[PickupDemand]) -> None:
        self.demand_table.setRowCount(0)
        grouped: dict[str, dict[str, int]] = {}
        priorities: dict[str, int] = {}
        for item in demands:
            plataforma = (item.plataforma or "").strip().upper()
            origem = (item.origem or "").strip().upper()
            if not plataforma:
                continue
            row = grouped.setdefault(plataforma, {"TMIB": 0, "M9": 0, "M1": 0})
            if origem in row:
                row[origem] += int(item.quantidade)
            priorities[plataforma] = max(int(item.prioridade), int(priorities.get(plataforma, 0)))
        for plataforma in sorted(grouped):
            row = self.demand_table.rowCount()
            self.demand_table.insertRow(row)
            values = [
                plataforma,
                str(int(grouped[plataforma]["TMIB"])),
                str(int(grouped[plataforma]["M9"])),
                str(int(grouped[plataforma]["M1"])),
                str(int(priorities.get(plataforma, 0))),
            ]
            for col, value in enumerate(values):
                self.demand_table.setItem(row, col, QTableWidgetItem(value))

    def _read_boat_states(self) -> List[PickupBoatState]:
        result: List[PickupBoatState] = []
        loaded_map = {item.nome: item for item in self._loaded_boat_states}
        for row in range(self.boats_table.rowCount()):
            nome = VersionEditor._text(self.boats_table, row, 1)
            if not nome:
                continue
            localizacao = VersionEditor._text(self.boats_table, row, 2) or "TMIB"
            base_state = loaded_map.get(nome)
            hora_disponivel = (base_state.hora_disponivel if base_state else "00:00") or "00:00"
            viagens = int(base_state.viagens_maximas if base_state else 1)
            rota_fixa = VersionEditor._text(self.boats_table, row, 3).strip()
            result.append(
                PickupBoatState(
                    nome=nome,
                    localizacao=localizacao,
                    hora_disponivel=hora_disponivel,
                    disponivel=VersionEditor._text(self.boats_table, row, 0).strip().upper() != "NAO",
                    viagens_maximas=max(0, viagens),
                    rota_fixa=rota_fixa,
                )
            )
        return result

    def _read_demands(self) -> List[PickupDemand]:
        result: List[PickupDemand] = []
        for row in range(self.demand_table.rowCount()):
            plataforma = VersionEditor._text(self.demand_table, row, 0).strip().upper()
            if not plataforma:
                continue
            try:
                tmib = int(VersionEditor._text(self.demand_table, row, 1).strip() or "0")
                m9 = int(VersionEditor._text(self.demand_table, row, 2).strip() or "0")
                m1 = int(VersionEditor._text(self.demand_table, row, 3).strip() or "0")
                prioridade = int(VersionEditor._text(self.demand_table, row, 4).strip() or "0")
            except ValueError:
                continue
            for origem, quantidade in (("TMIB", tmib), ("M9", m9), ("M1", m1)):
                if quantidade <= 0:
                    continue
                result.append(
                    PickupDemand(
                        plataforma=plataforma,
                        origem=origem,
                        quantidade=quantidade,
                        prioridade=max(0, prioridade),
                    )
                )
        return result

    def add_boat_row(self) -> None:
        available_names: List[str] = []
        if self.parent_window.current_op_config:
            current_names = {VersionEditor._text(self.boats_table, row, 1) for row in range(self.boats_table.rowCount())}
            available_names = [
                item.nome
                for item in self.parent_window.current_op_config.frota
                if item.nome not in current_names
            ]
        if not available_names:
            QMessageBox.information(self, "Recolhimento", "Nao ha embarcacoes adicionais para incluir.")
            return
        nome, ok = QInputDialog.getItem(self, "Incluir embarcacao", "Embarcacao:", available_names, 0, False)
        if not ok or not nome:
            return
        row = self.boats_table.rowCount()
        self.boats_table.insertRow(row)
        defaults = ["SIM", nome, "TMIB", ""]
        for col, value in enumerate(defaults):
            item = QTableWidgetItem(value)
            if col == 0:
                item.setTextAlignment(Qt.AlignCenter)
            self.boats_table.setItem(row, col, item)

    def add_demand_row(self) -> None:
        row = self.demand_table.rowCount()
        self.demand_table.insertRow(row)
        defaults = ["", "0", "0", "0", "0"]
        for col, value in enumerate(defaults):
            self.demand_table.setItem(row, col, QTableWidgetItem(value))
        self.demand_table.setCurrentCell(row, 0)

    def remove_demand_row(self) -> None:
        row = self.demand_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Recolhimento", "Selecione uma plataforma para excluir.")
            return
        self.demand_table.removeRow(row)

    def remove_boat_row(self) -> None:
        row = self.boats_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Recolhimento", "Selecione uma embarcacao para excluir.")
            return
        self.boats_table.removeRow(row)

    def export_pickup_csv(self) -> None:
        rows = []
        for row in range(self.demand_table.rowCount()):
            plataforma = VersionEditor._text(self.demand_table, row, 0).strip().upper()
            if not plataforma:
                continue
            rows.append(
                {
                    "PLATAFORMA": plataforma,
                    "TMIB": VersionEditor._text(self.demand_table, row, 1).strip() or "0",
                    "M9": VersionEditor._text(self.demand_table, row, 2).strip() or "0",
                    "M1": VersionEditor._text(self.demand_table, row, 3).strip() or "0",
                    "PRIORIDADE": VersionEditor._text(self.demand_table, row, 4).strip() or "0",
                }
            )
        if not rows:
            QMessageBox.warning(
                self,
                "Exportar CSV",
                "Nao ha demanda preenchida para exportar.",
            )
            return

        if self.parent_window.current_operation:
            default_name = f"{self.parent_window.current_operation.operacao_id}_recolhimento_demanda.csv"
        else:
            default_name = "recolhimento_demanda.csv"

        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar demanda de recolhimento em CSV",
            default_name,
            "CSV (*.csv)",
        )
        if not file_name:
            return

        output_path = Path(file_name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["PLATAFORMA", "TMIB", "M9", "M1", "PRIORIDADE"],
                delimiter=";",
            )
            writer.writeheader()
            writer.writerows(rows)
        QMessageBox.information(
            self,
            "Exportar CSV",
            f"Demanda de recolhimento exportada para:\n{output_path}",
        )

    def edit_boat_positions(self) -> None:
        if self.boats_table.rowCount() == 0:
            QMessageBox.warning(self, "Recolhimento", "Carregue uma operacao antes de alterar a posicao das lanchas.")
            return
        base_states = self._read_boat_states()
        dialog = PickupBoatPositionsDialog(base_states, self)
        if dialog.exec() != QDialog.Accepted:
            return
        positions = dialog.positions()
        for row in range(self.boats_table.rowCount()):
            nome = VersionEditor._text(self.boats_table, row, 1)
            if not nome or nome not in positions:
                continue
            item = QTableWidgetItem(positions[nome])
            self.boats_table.setItem(row, 2, item)

    def plan_pickup(self) -> None:
        if not self.parent_window.current_operation or not self.parent_window.current_root:
            QMessageBox.warning(self, "Recolhimento", "Selecione uma operacao.")
            return
        cutoff = self.surfer_cutoff_edit.text().strip()
        if not re.match(r"^\d{2}:\d{2}$", cutoff):
            QMessageBox.warning(self, "Recolhimento", "Informe o limite Surfer no formato HH:MM.")
            return
        try:
            boat_states = self._read_boat_states()
            pickup_demands = self._read_demands()
            result = self.service.plan_pickup(
                self.parent_window.current_root,
                self.parent_window.current_operation,
                self.version_combo.currentData(),
                boat_states,
                pickup_demands,
                cutoff,
                execution_mode="plan",
                now_hhmm=datetime.now().strftime("%H:%M"),
                pickup_engine=self.pickup_engine_combo.currentData(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Recolhimento", str(exc))
            return
        self.output_text.setPlainText(result.plan_text)

    def start_pickup_now(self) -> None:
        if not self.parent_window.current_operation or not self.parent_window.current_root:
            QMessageBox.warning(self, "Recolhimento", "Selecione uma operacao.")
            return
        cutoff = self.surfer_cutoff_edit.text().strip()
        if not re.match(r"^\d{2}:\d{2}$", cutoff):
            QMessageBox.warning(self, "Recolhimento", "Informe o limite Surfer no formato HH:MM.")
            return
        now_hhmm = datetime.now().strftime("%H:%M")
        include_late_fixed_routes: Optional[bool] = None
        if self.boats_table.rowCount() > 0:
            now_minutes = int(now_hhmm[:2]) * 60 + int(now_hhmm[3:])
            has_late_fixed = any(
                bool((item.rota_fixa or "").strip())
                and re.match(r"^\d{2}:\d{2}$", (item.hora_disponivel or "").strip())
                and (int(item.hora_disponivel[:2]) * 60 + int(item.hora_disponivel[3:])) <= now_minutes
                for item in self._read_boat_states()
            )
            if has_late_fixed:
                response = QMessageBox.question(
                    self,
                    "Iniciar recolhimento agora",
                    "Existem rotas fixas com horario ja passado. Deseja considera-las mesmo assim?",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.Yes,
                )
                if response == QMessageBox.Cancel:
                    return
                include_late_fixed_routes = response == QMessageBox.Yes
        try:
            boat_states = self._read_boat_states()
            pickup_demands = self._read_demands()
            result = self.service.plan_pickup(
                self.parent_window.current_root,
                self.parent_window.current_operation,
                self.version_combo.currentData(),
                boat_states,
                pickup_demands,
                cutoff,
                execution_mode="start_now",
                now_hhmm=now_hhmm,
                include_late_fixed_routes=include_late_fixed_routes,
                pickup_engine=self.pickup_engine_combo.currentData(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Recolhimento", str(exc))
            return
        self.output_text.setPlainText(result.plan_text)


class PaxSelectionDialog(QDialog):
    """Dialog to manually select which passengers board at a given stop."""

    def __init__(
        self,
        vessel: str,
        platform: str,
        destination: str,
        all_pax: list,
        pre_selected_names: set,
        locked_pax: "dict[str, str] | None" = None,
        max_at_stop: int = 9999,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{platform} → {destination}   [{vessel}]")
        self.setMinimumWidth(500)
        self.setMinimumHeight(420)
        self._max_at_stop = max_at_stop
        self._checkboxes: "dict[str, QCheckBox]" = {}

        layout = QVBoxLayout(self)

        header = QLabel(
            f"Passageiros em <b>{platform}</b> com destino <b>{destination}</b>."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        self._counter_label = QLabel()
        self._counter_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._counter_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setSpacing(4)

        locked = locked_pax or {}

        for entry in sorted(all_pax, key=lambda e: e.passenger_name.upper()):
            label_text = (
                f"{entry.passenger_name}  [{entry.passenger_id}]"
                if entry.passenger_id
                else entry.passenger_name
            )
            cb = QCheckBox(label_text)
            cb.setChecked(entry.passenger_name in pre_selected_names)
            cb.stateChanged.connect(self._on_check_changed)
            self._checkboxes[entry.passenger_name] = cb
            vbox.addWidget(cb)

        if locked:
            sep = QLabel("<hr><small><i>Em outra embarcação (somente leitura):</i></small>")
            sep.setWordWrap(True)
            vbox.addWidget(sep)
            for name in sorted(locked.keys()):
                other_vessel = locked[name]
                cb = QCheckBox(f"{name}  → {other_vessel}")
                cb.setEnabled(False)
                cb.setStyleSheet("color: gray;")
                vbox.addWidget(cb)

        vbox.addStretch()
        container.setLayout(vbox)
        scroll.setWidget(container)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_counter()

    def _on_check_changed(self) -> None:
        selected = sum(1 for cb in self._checkboxes.values() if cb.isChecked())
        at_limit = selected >= self._max_at_stop
        for cb in self._checkboxes.values():
            if not cb.isChecked():
                cb.setEnabled(not at_limit)
        self._update_counter()

    def _update_counter(self) -> None:
        selected = sum(1 for cb in self._checkboxes.values() if cb.isChecked())
        if self._max_at_stop < 9999:
            remaining = self._max_at_stop - selected
            color = "red" if remaining == 0 else "green" if remaining > 0 else "red"
            self._counter_label.setText(
                f'Selecionados: <span style="color:{color}"><b>{selected}</b></span>'
                f" / Máximo: <b>{self._max_at_stop}</b>"
                f" &nbsp; (vagas restantes: {max(0, remaining)})"
            )
        else:
            self._counter_label.setText(f"Selecionados: <b>{selected}</b>")

    def selected_names(self) -> set:
        return {name for name, cb in self._checkboxes.items() if cb.isChecked()}

class _AddPaxChooser(QDialog):
    """Small dialog to pick vessel + platform + destination before opening PaxSelectionDialog."""

    def __init__(self, vessels: list, platforms_dests: "dict[str, list[str]]", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Incluir passageiro")
        self.setMinimumWidth(340)
        self._platforms_dests = platforms_dests

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Selecione a embarcacao, plataforma e destino:"))

        form_layout = QHBoxLayout()

        self._vessel_combo = QComboBox()
        self._vessel_combo.addItems(vessels)

        self._platform_combo = QComboBox()
        self._platform_combo.addItems(sorted(platforms_dests.keys()))
        self._platform_combo.currentTextChanged.connect(self._update_dests)

        self._dest_combo = QComboBox()
        self._update_dests(self._platform_combo.currentText())

        for label_text, combo in [
            ("Embarcacao:", self._vessel_combo),
            ("Plataforma:", self._platform_combo),
            ("Destino:", self._dest_combo),
        ]:
            col = QVBoxLayout()
            col.addWidget(QLabel(label_text))
            col.addWidget(combo)
            form_layout.addLayout(col)

        layout.addLayout(form_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_dests(self, platform: str) -> None:
        self._dest_combo.clear()
        self._dest_combo.addItems(self._platforms_dests.get(platform, []))

    @property
    def vessel(self) -> str:
        return self._vessel_combo.currentText()

    @property
    def platform(self) -> str:
        return self._platform_combo.currentText()

    @property
    def destination(self) -> str:
        return self._dest_combo.currentText()


class _OriginsDialog(QDialog):
    """Shows the operator which platforms count as passenger origins, and audits them.

    TMIB and PCM-09 are always origins. Every other origin is dynamic: it follows the SOV,
    which is attached to whichever platform is designated to host its two-shift (day/night)
    crew, and that changes from day to day. Those live in
    `Configurações > Origens do Extrato` — the same list the recolhimento module uses — so
    this dialog only reports them. If they are stale the operator updates Configurações and
    processes again, keeping one source of truth.

    The Dados can tell when the list is stale: any passenger whose notas never mention a
    configured origin must be based somewhere else. That check runs here and names both the
    passengers affected and the platform the data points to.
    """

    def __init__(self, dados_rows: list, configured: set, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Origens dos passageiros")
        self.setMinimumWidth(560)
        self._dados_rows = dados_rows
        self._origins = configured

        from .distribuicao import audit_origins, suggest_origins
        from .offshore_pd.aliases import AliasResolver, canonical_to_operational

        resolver = AliasResolver()
        layout = QVBoxLayout(self)

        listed = ", ".join(sorted(canonical_to_operational(o) for o in configured))
        header = QLabel(f"Origens configuradas para este processamento: <b>{listed}</b>")
        header.setWordWrap(True)
        layout.addWidget(header)

        note = QLabel(
            "TMIB e M9 são sempre origens. As demais acompanham o SOV e são mantidas em "
            "Configurações > Origens do Extrato."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #64748b;")
        layout.addWidget(note)

        missing, ambiguous = audit_origins(dados_rows, configured, resolver)
        problems = []
        if missing:
            names = ", ".join(missing[:3])
            if len(missing) > 3:
                names += f" e mais {len(missing) - 3}"
            problems.append(f"{len(missing)} pax sem nenhuma origem ({names})")
        if ambiguous:
            names = ", ".join(ambiguous[:3])
            if len(ambiguous) > 3:
                names += f" e mais {len(ambiguous) - 3}"
            problems.append(f"{len(ambiguous)} pax com mais de uma origem ({names})")

        if problems:
            text = "A lista parece desatualizada: " + "; ".join(problems) + "."
            suggested = suggest_origins(dados_rows, resolver)
            faltando = [
                (c, n) for c, n in suggested if c not in configured
            ]
            if faltando:
                pistas = ", ".join(
                    f"{canonical_to_operational(c)} ({n} pax)" for c, n in faltando
                )
                text += f"\nOs dados apontam para: {pistas}."
            text += (
                "\n\nAtualize em Configurações > Origens do Extrato e processe de novo. "
                "Seguindo assim, as movimentações desses pax entram na tabela sem o teste "
                "de recolhimento — nenhuma é descartada em silêncio."
            )
            warn = QLabel(text)
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b91c1c;")
            layout.addWidget(warn)
        else:
            ok = QLabel(
                f"Auditoria: os {len(dados_rows)} registros se resolvem com essas origens — "
                "cada pax tem exatamente uma."
            )
            ok.setWordWrap(True)
            ok.setStyleSheet("color: #15803d;")
            layout.addWidget(ok)

        buttons = QHBoxLayout()
        buttons.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("Processar")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        buttons.addWidget(btn_cancel)
        buttons.addWidget(btn_ok)
        layout.addLayout(buttons)

    def origins(self) -> set:
        return self._origins


class _PaxSelectionDialog(QDialog):
    """Dialog shown when Dados pax count > operacao pax_disembark for a vessel leg.

    Displays the full pool of candidates with checkboxes, pre-selecting the first
    `limit` rows as the default (same as the auto-assignment). The operator can
    search by name and override the selection before confirming.
    """

    def __init__(self, group, parent=None):
        super().__init__(parent)
        self._group = group
        self._pool = group.pool
        horario_str = group.horario.strftime("%H:%M") if group.horario else "--:--"
        self.setWindowTitle(f"Selecionar Passageiros — {group.vessel} {horario_str}")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        horario_str = self._group.horario.strftime("%H:%M") if self._group.horario else "--:--"
        info = QLabel(
            f"<b>{self._group.vessel}</b> &nbsp;·&nbsp; {horario_str}"
            f" &nbsp;·&nbsp; {self._group.tipo_viagem or '-'}"
        )
        layout.addWidget(info)

        subtitle = QLabel(
            f"Selecione <b>{self._group.limit}</b> passageiro(s) "
            f"de <b>{len(self._pool)}</b> disponíveis "
            f"<small>— clique na linha para marcar ou desmarcar</small>"
        )
        layout.addWidget(subtitle)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Buscar:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrar por nome…")
        self._search.textChanged.connect(self._filter_rows)
        search_row.addWidget(self._search)
        layout.addLayout(search_row)

        self._counter_label = QLabel()
        layout.addWidget(self._counter_label)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels([_CHECK_MARK, "Nome Passageiro", "Orig → Dest"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setRowCount(len(self._pool))

        for i, fr in enumerate(self._pool):
            mark = QTableWidgetItem()
            mark.setFlags(Qt.ItemIsEnabled)
            mark.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 0, mark)
            self._table.setItem(i, 1, QTableWidgetItem(fr.dados_row.name or ""))
            orig = fr.dados_row.origem_raw or ""
            dest = fr.dados_row.destino_raw or ""
            self._table.setItem(i, 2, QTableWidgetItem(f"{orig} → {dest}"))
            _set_row_checked(self._table, i, i < self._group.limit)

        _setup_check_column(self._table)
        self._table.resizeColumnToContents(2)
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table)

        self._update_counter()

        btn_row = QHBoxLayout()
        btn_auto = QPushButton("Selecionar Automaticamente")
        btn_auto.setToolTip(f"Marcar os primeiros {self._group.limit} passageiros por ordem da planilha")
        btn_auto.clicked.connect(self._select_auto)
        btn_row.addWidget(btn_auto)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _filter_rows(self, text: str) -> None:
        text_lower = text.strip().lower()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            name = item.text().lower() if item else ""
            self._table.setRowHidden(row, bool(text_lower) and text_lower not in name)

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        """Clicar em qualquer lugar da linha marca ou desmarca — alvo bem maior que a caixinha."""
        _set_row_checked(self._table, row, not _is_row_checked(self._table, row))
        self._update_counter()

    def _update_counter(self) -> None:
        checked = sum(
            1 for row in range(self._table.rowCount())
            if _is_row_checked(self._table, row)
        )
        color = "green" if checked == self._group.limit else "red"
        self._counter_label.setText(
            f'<font color="{color}"><b>{checked}</b></font> / {self._group.limit} selecionados'
        )

    def _select_auto(self) -> None:
        for row in range(self._table.rowCount()):
            _set_row_checked(self._table, row, row < self._group.limit)
        self._update_counter()

    def get_selected(self) -> list:
        return [
            self._pool[row]
            for row in range(self._table.rowCount())
            if _is_row_checked(self._table, row)
        ]


class _AddTrechoDialog(QDialog):
    """Dialog to manually assign sob_demanda passengers to a vessel trip."""

    def __init__(self, sob_demanda_rows: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adicionar Trecho Manual")
        self.setMinimumWidth(620)
        self._rows = sob_demanda_rows
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Vessel + time
        top = QHBoxLayout()
        top.addWidget(QLabel("Embarcação:"))
        self._vessel_edit = QLineEdit()
        self._vessel_edit.setMinimumWidth(160)
        top.addWidget(self._vessel_edit)
        top.addWidget(QLabel("Horário (HH:MM):"))
        self._horario_edit = QLineEdit()
        self._horario_edit.setMaximumWidth(70)
        top.addWidget(self._horario_edit)
        top.addStretch()
        layout.addLayout(top)

        # Destination picker
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Destino:"))
        self._dest_combo = QComboBox()
        self._dest_combo.setMinimumWidth(160)
        destinos = sorted({fr.dados_row.destino_raw for fr in self._rows if fr.dados_row.destino_raw})
        self._dest_combo.addItems(destinos)
        self._dest_combo.currentIndexChanged.connect(self._refresh_pax)
        dest_row.addWidget(self._dest_combo)
        dest_row.addStretch()
        layout.addLayout(dest_row)

        # Passenger list with checkboxes
        self._pax_table = QTableWidget()
        self._pax_table.setColumnCount(2)
        self._pax_table.setHorizontalHeaderLabels([_CHECK_MARK, "Nome Passageiro"])
        self._pax_table.horizontalHeader().setStretchLastSection(True)
        self._pax_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._pax_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._pax_table.cellClicked.connect(self._on_pax_clicked)
        layout.addWidget(self._pax_table)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._refresh_pax()

    def _refresh_pax(self) -> None:
        dest = self._dest_combo.currentText()
        filtered = [fr for fr in self._rows if fr.dados_row.destino_raw == dest]
        self._pax_table.setRowCount(len(filtered))
        for i, fr in enumerate(filtered):
            mark = QTableWidgetItem()
            mark.setFlags(Qt.ItemIsEnabled)
            mark.setTextAlignment(Qt.AlignCenter)
            self._pax_table.setItem(i, 0, mark)
            self._pax_table.setItem(i, 1, QTableWidgetItem(fr.dados_row.name or ""))
            _set_row_checked(self._pax_table, i, False)
        _setup_check_column(self._pax_table)

    def _on_pax_clicked(self, row: int, _col: int) -> None:
        _set_row_checked(self._pax_table, row, not _is_row_checked(self._pax_table, row))

    def get_selection(self) -> tuple:
        dest = self._dest_combo.currentText()
        filtered = [fr for fr in self._rows if fr.dados_row.destino_raw == dest]
        selected = [
            filtered[i]
            for i in range(self._pax_table.rowCount())
            if _is_row_checked(self._pax_table, i)
        ]
        return self._vessel_edit.text().strip(), self._horario_edit.text().strip(), selected


class _TrocarViagemDialog(QDialog):
    """Escolhe para qual viagem programada o passageiro vai.

    Lista apenas as viagens que a operacao programou para a movimentacao dele — o filtro e o
    mesmo `_matches` do processamento —, entao nao ha como mandar o pax para uma lancha que
    nao passa no destino dele. A ocupacao de cada viagem e mostrada contra o
    QUANT PAX DESEMBARQUE, que e o limite que exige permuta.
    """

    _COLS = ["Embarcacao", "Horario", "Tipo", "Ocupacao", "Situacao"]

    def __init__(self, frs: list, atuais: dict, opcoes: list, parent=None):
        super().__init__(parent)
        self._opcoes = opcoes
        self.setWindowTitle("Trocar Viagem")
        self.setMinimumWidth(620)
        self.setMinimumHeight(380)

        layout = QVBoxLayout(self)

        if len(frs) == 1:
            dr = frs[0].dados_row
            layout.addWidget(QLabel(
                f"<b>{dr.name or '(sem nome)'}</b> &nbsp;·&nbsp; "
                f"{dr.origem_raw or '?'} → {dr.destino_raw or '?'}"
            ))
        else:
            movimentos = sorted({
                f"{fr.dados_row.origem_raw or '?'} → {fr.dados_row.destino_raw or '?'}"
                for fr in frs})
            layout.addWidget(QLabel(
                f"<b>{len(frs)} passageiros selecionados</b> &nbsp;·&nbsp; "
                + ", ".join(movimentos)))

        # De onde o lote sai. Com várias viagens de origem, mostra a conta de cada uma.
        origens: dict = {}
        for fr in frs:
            atual = atuais.get(id(fr))
            chave = ((atual.vessel, atual.horario) if atual else None)
            origens[chave] = origens.get(chave, 0) + 1
        partes = []
        for chave, n in sorted(origens.items(), key=lambda kv: str(kv[0])):
            if chave is None:
                partes.append(f"sem viagem ({n})" if len(frs) > 1 else "sem viagem")
            else:
                vessel, hor = chave
                h = hor.strftime("%H:%M") if hor else "--:--"
                partes.append(f"{vessel} · {h}" + (f" ({n})" if len(frs) > 1 else ""))
        layout.addWidget(QLabel("Viagem atual: " + ", ".join(partes)))
        layout.addWidget(QLabel(
            "<small>Só aparecem as viagens que a operação programou para "
            "<b>todos</b> os selecionados.</small>"))

        self._table = QTableWidget()
        self._table.setColumnCount(len(self._COLS))
        self._table.setHorizontalHeaderLabels(self._COLS)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setRowCount(len(opcoes))

        n = len(frs)
        for i, (slot, ocupados) in enumerate(opcoes):
            hor = slot.horario.strftime("%H:%M") if slot.horario else "--:--"
            livre = max(0, slot.limit - ocupados)
            if livre >= n:
                situacao = f"{livre} vaga(s) livre(s)"
            elif livre:
                situacao = f"{livre} livre(s) — permuta de {n - livre}"
            else:
                situacao = f"lotada — permuta de {n}"
            for col, val in enumerate([slot.vessel, hor, slot.tipo_viagem,
                                       f"{ocupados}/{slot.limit}", situacao]):
                item = QTableWidgetItem(val)
                if livre < n:
                    item.setForeground(_make_color("#b9770e"))
                self._table.setItem(i, col, item)

        self._table.resizeColumnsToContents()
        self._table.doubleClicked.connect(self.accept)
        layout.addWidget(self._table)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def escolhido(self):
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        return self._opcoes[rows[0].row()]


class _PermutaDialog(QDialog):
    """A viagem de destino nao tem vaga para todos: escolhe quem cede o lugar.

    Os candidatos sao os pax que estao na viagem de destino **e** que cabem em alguma das
    vagas que o lote esta liberando, senao a troca so empurraria o problema para o outro
    lado. Usa a mesma marca de selecao da janela de escolha de passageiros: check verde,
    linha inteira clicavel e busca por nome, porque escolher 24 numa lista de 24 na mao e
    exatamente o que estava lento.
    """

    def __init__(self, entram: list, destino_txt: str, vagas_txt: str,
                 precisa: int, candidatos: list, sem_vaga: int = 0, parent=None):
        super().__init__(parent)
        self._candidatos = candidatos
        self._precisa = precisa
        self.setWindowTitle("Permutar Passageiros")
        self.setMinimumWidth(620)
        self.setMinimumHeight(440)

        layout = QVBoxLayout(self)

        quem = (entram[0].dados_row.name or "(sem nome)" if len(entram) == 1
                else f"os {len(entram)} selecionados")
        cabecalho = QLabel(
            f"A viagem <b>{destino_txt}</b> não tem vaga para {quem}: a operação já "
            f"programou todas. Escolha <b>{precisa}</b> passageiro(s) para sair."
        )
        cabecalho.setWordWrap(True)
        layout.addWidget(cabecalho)

        destino_lbl = QLabel(
            f"<small>Quem sair assume as vagas que o lote está liberando: "
            f"{vagas_txt}</small>" if vagas_txt else
            "<small>Nenhuma vaga está sendo liberada, então quem sair fica "
            "<b>Sob demanda</b>.</small>"
        )
        destino_lbl.setWordWrap(True)
        layout.addWidget(destino_lbl)

        if sem_vaga:
            aviso = QLabel(
                f"<small><font color='#b9770e'>Atenção: {sem_vaga} do(s) que saírem "
                f"ficarão <b>Sob demanda</b> — há menos vagas liberadas do que gente "
                f"saindo.</font></small>")
            aviso.setWordWrap(True)
            layout.addWidget(aviso)

        busca_row = QHBoxLayout()
        busca_row.addWidget(QLabel("Buscar:"))
        self._busca = QLineEdit()
        self._busca.setPlaceholderText("Filtrar por nome…")
        self._busca.textChanged.connect(self._filtrar)
        busca_row.addWidget(self._busca)
        layout.addLayout(busca_row)

        self._contador = QLabel()
        layout.addWidget(self._contador)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(
            [_CHECK_MARK, "Nome Passageiro", "Orig → Dest", "Tipo"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setRowCount(len(candidatos))
        for i, fr in enumerate(candidatos):
            dr = fr.dados_row
            mark = QTableWidgetItem()
            mark.setFlags(Qt.ItemIsEnabled)
            mark.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 0, mark)
            self._table.setItem(i, 1, QTableWidgetItem(dr.name or ""))
            self._table.setItem(i, 2, QTableWidgetItem(
                f"{dr.origem_raw or ''} → {dr.destino_raw or ''}"))
            self._table.setItem(i, 3, QTableWidgetItem(fr.tipo_viagem or ""))
            _set_row_checked(self._table, i, i < precisa)
        _setup_check_column(self._table)
        self._table.resizeColumnToContents(2)
        self._table.cellClicked.connect(self._clicou)
        layout.addWidget(self._table)
        self._atualizar_contador()

        btn_row = QHBoxLayout()
        btn_filtrados = QPushButton("Marcar os filtrados")
        btn_filtrados.setToolTip(
            "Marca todos os que a busca está mostrando, sem mexer nos escondidos")
        btn_filtrados.clicked.connect(lambda: self._marcar_visiveis(True))
        btn_limpar = QPushButton("Desmarcar os filtrados")
        btn_limpar.clicked.connect(lambda: self._marcar_visiveis(False))
        btn_row.addWidget(btn_filtrados)
        btn_row.addWidget(btn_limpar)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _filtrar(self, texto: str) -> None:
        alvo = texto.strip().lower()
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 1)
            nome = item.text().lower() if item else ""
            self._table.setRowHidden(r, bool(alvo) and alvo not in nome)

    def _marcar_visiveis(self, marcar: bool) -> None:
        for r in range(self._table.rowCount()):
            if not self._table.isRowHidden(r):
                _set_row_checked(self._table, r, marcar)
        self._atualizar_contador()

    def _clicou(self, row: int, _col: int) -> None:
        _set_row_checked(self._table, row, not _is_row_checked(self._table, row))
        self._atualizar_contador()

    def _atualizar_contador(self) -> None:
        n = sum(1 for r in range(self._table.rowCount())
                if _is_row_checked(self._table, r))
        cor = "green" if n == self._precisa else "red"
        self._contador.setText(
            f'<font color="{cor}"><b>{n}</b></font> / {self._precisa} selecionados')

    def escolhidos(self) -> list:
        return [self._candidatos[r] for r in range(self._table.rowCount())
                if _is_row_checked(self._table, r)]


class _TrocaParesDialog(QDialog):
    """Duas listas lado a lado: quem o operador selecionou e quem pode entrar no lugar.

    Substitui, como caminho principal, a escolha da viagem de destino. O motivo veio do
    operador: para trocar por viagem ele precisa saber em que lancha esta o pax que quer
    trazer, e essa e exatamente a informacao que ele nao tem — acabava testando viagem por
    viagem. Aqui ele escolhe a **pessoa**, e a viagem vem de onde ela ja esta.

    Toda troca e 1 por 1, entao nenhuma viagem estoura nem esvazia e nao ha permuta para
    negociar: a lista da direita ja e o agregado de todas as lanchas. O caminho antigo
    continua a um botao de distancia, para o caso de mover alguem para uma viagem com vaga
    livre, em que nao ha par.
    """

    _MODO_PARES = "pares"
    _MODO_VIAGEM = "viagem"

    _ATIVO_BG = "#fff3cd"      # amarelo — a linha da esquerda que espera um substituto
    _PAR_BG = "#eafaf1"        # verde — par montado, o mesmo do check das outras janelas
    _INVALIDO_FG = "#9aa0a6"

    _COLS_ESQ = ["Nome Passageiro", "Orig → Dest", "Viagem atual", "Entra no lugar"]
    _COLS_DIR = ["Nome Passageiro", "Orig → Dest", "Viagem atual", "Sai no lugar de"]

    def __init__(self, frs: list, pool, parent=None):
        super().__init__(parent)
        self._frs = list(frs)
        self._pool = pool
        self._pares: dict = {}        # id(selecionado) -> candidato
        self._usados: dict = {}       # id(candidato)   -> selecionado
        self._ativo = None            # indice em self._frs
        # (coluna, crescente) por lado, ou None para a ordem de entrada. A ordenacao e
        # nossa, nao a do Qt: o `_render` reconstroi as duas tabelas a cada clique e o
        # `setSortingEnabled` perderia a ordem justamente ai.
        self._ordem_esq = None
        self._ordem_dir = None
        # Por identidade: FilledRow e dataclass com __eq__, entao list.index() pode
        # devolver a posicao de uma linha equivalente em vez da propria.
        self._pos_esq = {id(fr): i for i, fr in enumerate(self._frs)}
        self._pos_dir = {id(fr): i for i, fr in enumerate(pool.candidatos)}
        self.modo = self._MODO_PARES

        self.setWindowTitle("Trocar Viagem")
        self.setMinimumWidth(1020)
        self.setMinimumHeight(560)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"<b>{len(frs)} passageiro(s) selecionado(s)</b> &nbsp;·&nbsp; "
            f"{len(pool.candidatos)} disponível(is) para troca"))
        self._hint = QLabel("")
        self._hint.setStyleSheet("color: #475569;")
        layout.addWidget(self._hint)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._painel(
            "1 · Selecionados — clique em quem <b>não</b> deveria estar aí",
            self._COLS_ESQ, "esq"))
        splitter.addWidget(self._painel(
            "2 · Disponíveis (todas as lanchas) — clique em quem entra no lugar",
            self._COLS_DIR, "dir"))
        splitter.setSizes([500, 520])
        layout.addWidget(splitter, 1)

        rodape = QHBoxLayout()
        self._contador = QLabel("")
        self._contador.setStyleSheet("font-weight: bold;")
        rodape.addWidget(self._contador)
        rodape.addStretch()
        btn_desfazer = QPushButton("Desfazer par")
        btn_desfazer.clicked.connect(self._desfazer)
        rodape.addWidget(btn_desfazer)
        btn_limpar = QPushButton("Limpar todos")
        btn_limpar.clicked.connect(self._limpar)
        rodape.addWidget(btn_limpar)
        btn_viagem = QPushButton("Mover para outra viagem...")
        btn_viagem.setToolTip(
            "Caminho antigo: escolher a viagem de destino em vez do passageiro.\n"
            "Serve para mover alguém para uma viagem com vaga livre, sem par.")
        btn_viagem.clicked.connect(self._ir_para_viagem)
        rodape.addWidget(btn_viagem)
        layout.addLayout(rodape)

        self._btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._btns.button(QDialogButtonBox.Ok).setText("Aplicar trocas")
        self._btns.accepted.connect(self.accept)
        self._btns.rejected.connect(self.reject)
        layout.addWidget(self._btns)

        self._render()

    # ----- construcao -------------------------------------------------------------
    def _painel(self, titulo: str, cols: list, lado: str):
        caixa = QWidget()
        v = QVBoxLayout(caixa)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel(titulo))

        busca = QLineEdit()
        busca.setPlaceholderText("Buscar nome...")
        busca.textChanged.connect(self._render)
        v.addWidget(busca)

        tabela = QTableWidget()
        tabela.setColumnCount(len(cols))
        tabela.setHorizontalHeaderLabels(cols)
        tabela.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # A marca de escolha e o fundo da linha, pintado por nos. A selecao nativa do Qt
        # competiria com ela e o operador ja disse que o realce nativo e pouco visivel.
        tabela.setSelectionMode(QAbstractItemView.NoSelection)
        tabela.verticalHeader().setVisible(False)
        tabela.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        tabela.horizontalHeader().setStretchLastSection(True)
        tabela.cellClicked.connect(
            self._clique_esq if lado == "esq" else self._clique_dir)
        cabecalho = tabela.horizontalHeader()
        cabecalho.setToolTip("Clique no cabeçalho para ordenar por esta coluna.")
        cabecalho.setSortIndicatorShown(True)
        cabecalho.setSectionsClickable(True)
        cabecalho.sectionClicked.connect(
            lambda col, l=lado: self._ordenar(l, col))
        v.addWidget(tabela)

        if lado == "esq":
            self._tab_esq, self._busca_esq = tabela, busca
        else:
            self._tab_dir, self._busca_dir = tabela, busca
        return caixa

    # ----- texto ------------------------------------------------------------------
    @staticmethod
    def _mov_txt(fr) -> str:
        dr = fr.dados_row
        return f"{dr.origem_raw or '?'} → {dr.destino_raw or '?'}"

    @staticmethod
    def _viagem_txt(fr) -> str:
        if not fr.embarcacao:
            return "sem viagem"
        hor = fr.horario.strftime("%H:%M") if fr.horario else "--:--"
        n = f" · v{fr.n_viagem}" if fr.n_viagem else ""
        return f"{fr.embarcacao} · {hor}{n}"

    def _valores_esq(self, fr) -> list:
        par = self._pares.get(id(fr))
        return [fr.dados_row.name or "", self._mov_txt(fr), self._viagem_txt(fr),
                (f"{par.dados_row.name or ''} ({self._viagem_txt(par)})" if par else "")]

    def _valores_dir(self, cand) -> list:
        dono = self._usados.get(id(cand))
        return [cand.dados_row.name or "", self._mov_txt(cand), self._viagem_txt(cand),
                (dono.dados_row.name or "") if dono else ""]

    # ----- ordenacao ----------------------------------------------------------------
    @staticmethod
    def _chave_ordem(texto: str) -> str:
        """Sem acento e em caixa alta: senao ANDRÉ cai depois de ANTONIO."""
        sem_acento = unicodedata.normalize("NFKD", texto or "")
        return "".join(c for c in sem_acento if not unicodedata.combining(c)).upper()

    def _ordenar(self, lado: str, col: int) -> None:
        atual = self._ordem_esq if lado == "esq" else self._ordem_dir
        nova = (col, not atual[1]) if atual and atual[0] == col else (col, True)
        if lado == "esq":
            self._ordem_esq = nova
        else:
            self._ordem_dir = nova
        self._render()

    def _ordenados(self, frs: list, ordem, valores) -> list:
        if ordem is None:
            return frs
        col, crescente = ordem
        return sorted(frs, key=lambda fr: self._chave_ordem(valores(fr)[col]),
                      reverse=not crescente)

    # ----- render -----------------------------------------------------------------
    def _visiveis(self, frs: list, busca, ordem, valores) -> list:
        termo = busca.text().strip().lower()
        if termo:
            frs = [fr for fr in frs if termo in (fr.dados_row.name or "").lower()]
        return self._ordenados(list(frs), ordem, valores)

    def _render(self) -> None:
        from .distribuicao.filler import can_swap_pair

        ativo = self._frs[self._ativo] if self._ativo is not None else None

        visiveis = self._visiveis(self._frs, self._busca_esq, self._ordem_esq,
                                  self._valores_esq)
        self._tab_esq.setRowCount(len(visiveis))
        for r, fr in enumerate(visiveis):
            fundo = (self._ATIVO_BG if fr is ativo
                     else self._PAR_BG if self._pares.get(id(fr)) else None)
            self._preenche(self._tab_esq, r, self._valores_esq(fr), self._pos_esq[id(fr)],
                           fundo, negrito=fr is ativo)

        visiveis = self._visiveis(self._pool.candidatos, self._busca_dir, self._ordem_dir,
                                  self._valores_dir)
        self._tab_dir.setRowCount(len(visiveis))
        for r, cand in enumerate(visiveis):
            dono = self._usados.get(id(cand))
            # Cinza = a operacao nao programou nenhuma viagem que sirva aos dois. O pax
            # continua visivel, para o operador nao procurar por alguem que sumiu da lista.
            invalido = ativo is not None and not can_swap_pair(self._pool, ativo, cand)
            self._preenche(self._tab_dir, r, self._valores_dir(cand),
                           self._pos_dir[id(cand)],
                           self._PAR_BG if dono else None,
                           cinza=invalido and not dono)

        for tabela, ordem in ((self._tab_esq, self._ordem_esq),
                              (self._tab_dir, self._ordem_dir)):
            tabela.resizeColumnsToContents()
            cabecalho = tabela.horizontalHeader()
            cabecalho.setSectionResizeMode(0, QHeaderView.Stretch)
            if ordem is not None:
                cabecalho.setSortIndicator(
                    ordem[0], Qt.AscendingOrder if ordem[1] else Qt.DescendingOrder)

        n = len(self._pares)
        self._contador.setText(f"{n} troca(s) montada(s)")
        self._btns.button(QDialogButtonBox.Ok).setEnabled(bool(n))
        if ativo is None:
            self._hint.setText("Escolha na lista da esquerda quem deve <b>sair</b> da viagem.")
        else:
            self._hint.setText(
                f"<b>{ativo.dados_row.name or '(sem nome)'}</b> sai de "
                f"{self._viagem_txt(ativo)} — escolha à direita quem entra no lugar. "
                "Quem aparece em cinza não pode: a operação não programou viagem que sirva "
                "aos dois.")

    def _preenche(self, tabela, linha: int, valores: list, indice: int,
                  fundo: str | None, negrito: bool = False, cinza: bool = False) -> None:
        for col, val in enumerate(valores):
            item = QTableWidgetItem(val)
            item.setData(Qt.UserRole, indice)
            if fundo:
                item.setBackground(_make_color(fundo))
            if cinza:
                item.setForeground(_make_color(self._INVALIDO_FG))
            if negrito:
                fonte = item.font()
                fonte.setBold(True)
                item.setFont(fonte)
            tabela.setItem(linha, col, item)

    # ----- interacao --------------------------------------------------------------
    @staticmethod
    def _indice(tabela, linha: int):
        item = tabela.item(linha, 0)
        return None if item is None else item.data(Qt.UserRole)

    def _clique_esq(self, linha: int, _col: int) -> None:
        idx = self._indice(self._tab_esq, linha)
        if idx is None:
            return
        self._ativo = None if idx == self._ativo else idx
        self._render()

    def _clique_dir(self, linha: int, _col: int) -> None:
        from .distribuicao.filler import can_swap_pair

        idx = self._indice(self._tab_dir, linha)
        if idx is None:
            return
        cand = self._pool.candidatos[idx]
        dono = self._usados.get(id(cand))

        def desfaz() -> None:
            self._pares.pop(id(dono), None)
            self._usados.pop(id(cand), None)

        if self._ativo is None:
            if dono is not None:                 # sem ninguem ativo, clicar num par desfaz
                desfaz()
                self._render()
                return
            self._hint.setText(
                "<b>Escolha primeiro, na lista da esquerda, quem deve sair.</b>")
            return

        atual = self._frs[self._ativo]
        if dono is atual:                        # clicar no proprio par desfaz
            desfaz()
            self._render()
            return
        if not can_swap_pair(self._pool, atual, cand):
            self._hint.setText(
                f"<b style='color:#b91c1c'>A operação não programou nenhuma viagem que "
                f"sirva a {atual.dados_row.name or '?'} e a "
                f"{cand.dados_row.name or '?'} ao mesmo tempo.</b>")
            return

        # Com alguem ativo, clicar num candidato ja usado significa passa-lo para a linha
        # ativa — nunca desfazer em silencio e deixar a linha ativa sem par.
        if dono is not None:
            desfaz()
        anterior = self._pares.get(id(atual))
        if anterior is not None:
            self._usados.pop(id(anterior), None)
        self._pares[id(atual)] = cand
        self._usados[id(cand)] = atual
        # Sem avanco automatico de propósito: com a lista da direita filtrada por busca, um
        # avanco silencioso faria o proximo clique parear a linha errada.
        self._ativo = None
        self._render()

    def _desfazer(self) -> None:
        if self._ativo is None:
            return
        cand = self._pares.pop(id(self._frs[self._ativo]), None)
        if cand is not None:
            self._usados.pop(id(cand), None)
        self._render()

    def _limpar(self) -> None:
        self._pares.clear()
        self._usados.clear()
        self._ativo = None
        self._render()

    def _ir_para_viagem(self) -> None:
        self.modo = self._MODO_VIAGEM
        self.accept()

    def pares(self) -> list:
        """[(quem sai, quem entra)] na ordem da lista da esquerda."""
        return [(fr, self._pares[id(fr)]) for fr in self._frs if id(fr) in self._pares]


class ManifestosDistribuicaoTab(QWidget):
    _STATUS_COLORS = {
        "auto":          "#d4edda",  # verde claro
        "ambiguous":     "#fff3cd",  # amarelo
        "sob_demanda":   "#fde8d8",  # laranja claro
        "already_filled":"#eceff1",  # cinza — veio pronto da planilha
    }
    _STATUS_LABELS = {
        "auto":          "Auto",
        "ambiguous":     "Ambiguo",
        "sob_demanda":   "Sob demanda",
        "already_filled":"Ja preenchido",
    }
    _COLS = ["Nome Passageiro", "Origem", "Destino",
             "Embarcacao", "Horario", "Nº Viagem", "Tipo", "Status"]

    def __init__(self, parent_window: "MainWindow"):
        super().__init__()
        self.parent_window = parent_window
        self._filled_rows: list = []
        self._n_viagem_map: dict = {}
        self._trips: list = []
        self._visible_rows: list = []
        # Trocas manuais valem so nesta sessao: quem as guarda e a planilha. Reprocessar
        # relê o Dados e refaz a distribuicao, entao sem salvar antes elas se perdem — o
        # Processar avisa quando ha troca pendente.
        self._trocas_pendentes = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # --- File pickers ---
        form = QFormLayout()

        dados_row = QHBoxLayout()
        self._dados_path = QLineEdit()
        self._dados_path.setPlaceholderText("Caminho da planilha Dados (.xlsx)")
        btn_dados = QPushButton("Procurar")
        btn_dados.clicked.connect(self._browse_dados)
        dados_row.addWidget(self._dados_path)
        dados_row.addWidget(btn_dados)
        dados_widget = QWidget()
        dados_widget.setLayout(dados_row)
        form.addRow("Planilha Dados:", dados_widget)

        operacao_row = QHBoxLayout()
        self._operacao_path = QLineEdit()
        self._operacao_path.setPlaceholderText("Caminho da planilha Operacao (.xlsx)")
        btn_op = QPushButton("Procurar")
        btn_op.clicked.connect(self._browse_operacao)
        operacao_row.addWidget(self._operacao_path)
        operacao_row.addWidget(btn_op)
        operacao_widget = QWidget()
        operacao_widget.setLayout(operacao_row)
        form.addRow("Planilha Operacao:", operacao_widget)

        layout.addLayout(form)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_processar = QPushButton("Processar")
        btn_processar.clicked.connect(self._processar)
        self._btn_salvar = QPushButton("Salvar na planilha")
        self._btn_salvar.clicked.connect(self._salvar)
        self._btn_salvar.setEnabled(False)
        # Inativado a pedido do operador: nunca teve uso na pratica. O codigo do botao, do
        # `_adicionar_trecho` e do `_AddTrechoDialog` fica, e basta tirar o setVisible para
        # trazer de volta. Mesmo tratamento das abas Recolhimento e Manifestos Recolhimento.
        self._btn_add_trecho = QPushButton("Adicionar Trecho")
        self._btn_add_trecho.clicked.connect(self._adicionar_trecho)
        self._btn_add_trecho.setEnabled(False)
        self._btn_add_trecho.setVisible(False)
        self._btn_trocar = QPushButton("Trocar Viagem")
        self._btn_trocar.setToolTip(
            "Abre as duas listas lado a lado: os passageiros selecionados e, agregados de\n"
            "todas as lanchas, os que podem trocar de lugar com eles."
        )
        self._btn_trocar.clicked.connect(self._trocar_viagem)
        self._btn_trocar.setEnabled(False)
        self._btn_comparar = QPushButton("Comparar com PDFs")
        self._btn_comparar.clicked.connect(self._comparar_pdfs)
        self._btn_comparar.setEnabled(False)
        self._lbl_status = QLabel("")
        btn_row.addWidget(btn_processar)
        btn_row.addWidget(self._btn_salvar)
        btn_row.addWidget(self._btn_add_trecho)
        btn_row.addWidget(self._btn_trocar)
        btn_row.addWidget(self._btn_comparar)
        btn_row.addStretch()
        btn_row.addWidget(self._lbl_status)
        layout.addLayout(btn_row)

        # --- Filter bar ---
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filtrar:"))

        self._filter_nome = QLineEdit()
        self._filter_nome.setPlaceholderText("Nome...")
        self._filter_nome.setMaximumWidth(180)
        self._filter_nome.textChanged.connect(self._apply_filters)
        filter_row.addWidget(QLabel("Nome:"))
        filter_row.addWidget(self._filter_nome)

        self._filter_embarcacao = QComboBox()
        self._filter_embarcacao.setMinimumWidth(140)
        self._filter_embarcacao.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(QLabel("Embarcacao:"))
        filter_row.addWidget(self._filter_embarcacao)

        # Filtrar por destino é o caminho natural para isolar um lote homogêneo — foi a
        # falta dele que fez a seleção dos 24 do TMIB para o M9 pegar junto um pax que ia
        # para o B1, e aí nenhuma viagem atendia todo mundo.
        self._filter_destino = QComboBox()
        self._filter_destino.setMinimumWidth(100)
        self._filter_destino.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(QLabel("Destino:"))
        filter_row.addWidget(self._filter_destino)

        self._filter_tipo = QComboBox()
        self._filter_tipo.setMinimumWidth(160)
        self._filter_tipo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(QLabel("Tipo:"))
        filter_row.addWidget(self._filter_tipo)

        self._filter_n_viagem = QComboBox()
        self._filter_n_viagem.setMinimumWidth(80)
        self._filter_n_viagem.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(QLabel("Nº Viagem:"))
        filter_row.addWidget(self._filter_n_viagem)

        btn_limpar = QPushButton("Limpar filtros")
        btn_limpar.clicked.connect(self._clear_filters)
        filter_row.addWidget(btn_limpar)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # --- Preview table ---
        self._table = QTableWidget()
        # O realce da selecao e definido por objectName na folha de estilo da janela: cada
        # linha ja tem um fundo proprio (a cor do status) e, quando o foco sai da tabela —
        # que e o que acontece assim que o operador clica em Trocar Viagem —, o Qt pinta a
        # selecao com o grupo Inactive da paleta, um cinza que some sobre essas cores.
        self._table.setObjectName("tabelaDistribuicao")
        self._table.setColumnCount(len(self._COLS))
        self._table.setHorizontalHeaderLabels(self._COLS)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table)

    def _browse_dados(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar planilha Dados", "", "Excel (*.xlsx)")
        if path:
            self._dados_path.setText(path)

    def _browse_operacao(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar planilha Operacao", "", "Excel (*.xlsx)")
        if path:
            self._operacao_path.setText(path)

    def _configured_origins(self) -> set:
        """Canonical passenger origins: TMIB and M9 always, plus the active extrato origins.

        Reads `Configurações > Origens do Extrato`, the same list the recolhimento module
        consumes, so both modules agree on what an origin is. Note the recolhimento side
        also hardcodes M1; here only TMIB and M9 are implicit, because M1 is a work platform
        in the current operation and forcing it in makes passengers ambiguous.
        """
        from .distribuicao import FIXED_ORIGINS
        from .offshore_pd.aliases import AliasResolver

        resolver = AliasResolver()
        origins = set(FIXED_ORIGINS)
        op_config = getattr(self.parent_window, "current_op_config", None)
        for item in getattr(op_config, "origens_extrato", []) or []:
            if not getattr(item, "ativa", True):
                continue
            code = str(getattr(item, "codigo", "")).strip()
            if not code:
                continue
            try:
                origins.add(resolver.canonical(code))
            except (ValueError, AttributeError):
                continue
        return origins

    def _processar(self) -> None:
        from .distribuicao import (
            PLATFORM_EQUIVALENCES,
            fill_rows,
            parse_operacao,
            read_dados,
        )
        from .distribuicao.filler import _assign_n_viagem

        dados_path = self._dados_path.text().strip()
        operacao_path = self._operacao_path.text().strip()

        if not dados_path or not operacao_path:
            QMessageBox.warning(self, "Manifestos Distribuicao", "Informe os caminhos das duas planilhas.")
            return

        if self._trocas_pendentes:
            resp = QMessageBox.question(
                self, "Trocas não salvas",
                f"Você fez {self._trocas_pendentes} troca(s) de viagem que ainda não foram "
                "salvas na planilha.\n\nReprocessar refaz a distribuição do zero e essas "
                "trocas serão perdidas.\n\nDeseja continuar?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return

        try:
            legs = parse_operacao(operacao_path)
            dados_rows = read_dados(dados_path)
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao ler planilhas", str(exc))
            return

        # The dynamic origins follow the SOV and change daily; show what is configured so
        # the operator can fix Configuracoes and re-run before anything is assigned.
        origins = self._configured_origins()
        origins_dlg = _OriginsDialog(dados_rows, origins, parent=self)
        if origins_dlg.exec() != QDialog.Accepted:
            return

        try:
            self._filled_rows, sel_groups, n_viagem_map = fill_rows(
                dados_rows, legs,
                extra_aliases=PLATFORM_EQUIVALENCES,
                origins=origins,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao processar", str(exc))
            return

        self._n_viagem_map = n_viagem_map
        self._trips = legs
        self._trocas_pendentes = 0

        # Show a selection dialog for each contested group
        for group in sel_groups:
            dlg = _PaxSelectionDialog(group, parent=self)
            if dlg.exec() == QDialog.Accepted:
                self._apply_selection_group(group, dlg.get_selected())
            # If cancelled: keep the pre-assigned default

        # Re-run n_viagem assignment after any operator changes
        _assign_n_viagem(self._filled_rows, n_viagem_map)

        self._populate_table()
        self._btn_salvar.setEnabled(True)
        self._btn_trocar.setEnabled(True)
        self._btn_comparar.setEnabled(True)

        self._lbl_status.setText(self._status_text())

    def _status_text(self) -> str:
        auto = sum(1 for fr in self._filled_rows if fr.status == "auto")
        amb = sum(1 for fr in self._filled_rows if fr.status == "ambiguous")
        sob = sum(1 for fr in self._filled_rows if fr.status == "sob_demanda")
        ja = sum(1 for fr in self._filled_rows if fr.status == "already_filled")
        # "Ja preenchido" so aparece quando existe: numa rodada do zero nao ha nenhuma.
        ja_txt = f"  Ja preenchido: {ja}" if ja else ""
        return (f"{len(self._filled_rows)} linhas | Auto: {auto}  Ambiguo: {amb}  "
                f"Sob demanda: {sob}{ja_txt}")

    @staticmethod
    def _apply_selection_group(group, selected_frs: list) -> None:
        """Apply operator selection: assign selected to this vessel, cascade rest."""
        selected_ids = {id(fr) for fr in selected_frs}

        # Reset only the rows this group had pre-assigned. Other rows in the pool may
        # belong to a different vessel that legitimately claimed them, and wiping those
        # would drop their embarcacao/horario/n_viagem.
        for fr in group.assigned or group.pool:
            fr.embarcacao = None
            fr.horario = None
            fr.n_viagem = None
            fr.candidates = []      # senão a perna que o reivindicou continua aparecendo
            fr.status = "sob_demanda"

        for fr in selected_frs:
            fr.embarcacao = group.vessel
            fr.horario = group.horario
            fr.status = "auto"

        # Cascade unselected pax to subsequent candidate legs
        dest_c = group.pool[0].destino_canonical if group.pool else None
        unselected = [fr for fr in group.pool if id(fr) not in selected_ids]
        for leg in group.remaining_candidates:
            if not unselected:
                break
            if dest_c and leg.destination_canonical == dest_c and leg.pax_disembark is not None:
                limit = leg.pax_disembark
            else:
                limit = len(unselected)
            auto_count = min(len(unselected), limit)
            for fr in unselected[:auto_count]:
                fr.embarcacao = leg.vessel
                fr.horario = leg.departure_time
                fr.status = "auto"
            unselected = unselected[auto_count:]
        # Any still unselected remain sob_demanda (already set above)

    def _populate_table(self) -> None:
        # A tabela mostra a programação do dia inteira, inclusive o que a planilha já trouxe
        # preenchido. Antes escondia essas linhas, e como a troca trabalha em cima da linha
        # selecionada, depois de salvar e reprocessar não havia mais o que trocar.
        new_rows = list(self._filled_rows)
        # O Qt.UserRole da coluna 0 guarda o índice nesta lista, e o índice sobrevive à
        # ordenação da tabela — é assim que a troca acha o passageiro selecionado.
        self._visible_rows = new_rows
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(new_rows))

        embarcacoes: set = set()
        tipos: set = set()
        n_viagens: set = set()
        destinos: set = set()

        for row_idx, fr in enumerate(new_rows):
            dr = fr.dados_row
            horario_str = fr.horario.strftime("%H:%M") if fr.horario else ""
            n_str = str(fr.n_viagem) if fr.n_viagem else ""
            # A coluna mostra SÓ a embarcação de verdade. Antes caía para `fr.candidates`
            # quando a embarcação estava vazia, e como o `_salvar` lê os valores DA TABELA,
            # esse palpite virava dado gravado: o pax que o operador tinha desmarcado no
            # diálogo saía na planilha com EMBARCAÇÃO preenchida e horário e nº de viagem em
            # branco, parecendo programado pela metade.
            embarcacao_display = fr.embarcacao or ""
            status_label = self._STATUS_LABELS.get(fr.status, fr.status)

            if embarcacao_display:
                embarcacoes.add(embarcacao_display)
            if dr.destino_raw:
                destinos.add(dr.destino_raw)
            if fr.tipo_viagem:
                tipos.add(fr.tipo_viagem)
            if n_str:
                n_viagens.add(n_str)

            values = [
                dr.name or "",
                dr.origem_raw or "",
                dr.destino_raw or "",
                embarcacao_display,
                horario_str,
                n_str,
                fr.tipo_viagem or "",
                status_label,
            ]

            color = self._STATUS_COLORS.get(fr.status)
            for col_idx, val in enumerate(values):
                item = QTableWidgetItem(val)
                if col_idx == 0:
                    item.setData(Qt.UserRole, row_idx)
                if color and col_idx < len(self._COLS) - 1:
                    item.setBackground(_make_color(color))
                if col_idx == len(self._COLS) - 1:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row_idx, col_idx, item)

        self._table.resizeColumnsToContents()
        self._table.setSortingEnabled(True)
        self._populate_filter_combos(
            sorted(embarcacoes), sorted(tipos),
            sorted(n_viagens, key=lambda x: int(x) if x.isdigit() else 999),
            sorted(destinos),
        )

    def _populate_filter_combos(self, embarcacoes: list, tipos: list, n_viagens: list,
                                destinos: list | None = None) -> None:
        for combo, values in [
            (self._filter_embarcacao, embarcacoes),
            (self._filter_destino, destinos or []),
            (self._filter_tipo, tipos),
            (self._filter_n_viagem, n_viagens),
        ]:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(Todas)")
            combo.addItems(values)
            combo.blockSignals(False)

    def _apply_filters(self) -> None:
        nome_filter = self._filter_nome.text().strip().lower()
        emb_filter = self._filter_embarcacao.currentText()
        dest_filter = self._filter_destino.currentText()
        tipo_filter = self._filter_tipo.currentText()
        n_filter = self._filter_n_viagem.currentText()

        for row in range(self._table.rowCount()):
            def cell(col: int, _row: int = row) -> str:
                item = self._table.item(_row, col)
                return item.text() if item else ""

            show = True
            if nome_filter and nome_filter not in cell(0).lower():
                show = False
            if emb_filter != "(Todas)" and cell(3) != emb_filter:
                show = False
            if dest_filter != "(Todas)" and cell(2) != dest_filter:
                show = False
            if tipo_filter != "(Todas)" and cell(6) != tipo_filter:
                show = False
            if n_filter != "(Todas)" and cell(5) != n_filter:
                show = False

            self._table.setRowHidden(row, not show)

    def _clear_filters(self) -> None:
        self._filter_nome.clear()
        for combo in (self._filter_embarcacao, self._filter_destino,
                      self._filter_tipo, self._filter_n_viagem):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._apply_filters()

    def _salvar(self) -> None:
        from .distribuicao import write_dados
        from datetime import time as dt_time

        dados_path = self._dados_path.text().strip()
        # A MESMA lista que o `_populate_table` usou, senão o índice guardado no UserRole
        # aponta para outra pessoa.
        new_rows = self._visible_rows

        # Sync table edits back — use UserRole to map visual rows to original indices
        # (table may be sorted, so visual row != insertion order)
        for table_row in range(self._table.rowCount()):
            item0 = self._table.item(table_row, 0)
            if not item0:
                continue
            orig_idx = item0.data(Qt.UserRole)
            if orig_idx is None or orig_idx >= len(new_rows):
                continue
            fr = new_rows[orig_idx]

            def cell(col: int, _row: int = table_row) -> str:
                item = self._table.item(_row, col)
                return item.text().strip() if item else ""

            antes = (fr.embarcacao, fr.horario, fr.n_viagem, fr.tipo_viagem)

            fr.embarcacao = cell(3) or None
            horario_text = cell(4)
            try:
                h, m = horario_text.split(":")
                fr.horario = dt_time(int(h), int(m))
            except (ValueError, AttributeError):
                fr.horario = None
            n_text = cell(5)
            fr.n_viagem = int(n_text) if n_text.isdigit() else None
            fr.tipo_viagem = cell(6) or None

            # O `write_dados` pula as linhas `already_filled`, porque elas vieram prontas da
            # planilha e reescrevê-las seria à toa. Agora que essas linhas aparecem na tabela,
            # elas podem ser trocadas ou editadas à mão — e aí precisam ser gravadas, senão a
            # alteração se perde em silêncio no Salvar.
            if fr.status == "already_filled" and (
                fr.embarcacao, fr.horario, fr.n_viagem, fr.tipo_viagem) != antes:
                fr.status = "auto"

        try:
            write_dados(dados_path, self._filled_rows)
            self._trocas_pendentes = 0     # a planilha passou a ser a guardiã das trocas
            QMessageBox.information(self, "Manifestos Distribuicao", f"Planilha salva em:\n{dados_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao salvar", str(exc))

    @staticmethod
    def _dados_date(dados_rows):
        """Data da planilha Dados, para não comparar com PDFs de outro dia."""
        from datetime import datetime

        for row in dados_rows:
            texto = (row.date_str or "").strip()
            for fmt in ("%d.%m.%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(texto, fmt).date()
                except ValueError:
                    continue
        return None

    def _comparar_pdfs(self) -> None:
        """Confere se cada movimentação programada consta também nos PDFs oficiais."""
        from .distribuicao import (
            comparar,
            find_programacao_pdfs,
            formatar_relatorio,
            parse_lanchas_pdfs,
            programacao_da_planilha,
            read_dados,
        )

        dados_path = self._dados_path.text().strip()
        if not dados_path:
            QMessageBox.warning(self, "Comparar com PDFs", "Informe a planilha Dados.")
            return

        # Lê a planilha do disco: a comparação é do que está gravado, não do que esta sessão
        # calculou. Só assim apagar uma programação no Excel aparece no relatório.
        try:
            dados_rows = read_dados(dados_path)
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao ler a planilha Dados", str(exc))
            return

        sheet_rows = programacao_da_planilha(dados_rows)

        folder = QFileDialog.getExistingDirectory(
            self, "Selecionar a pasta com as programações em PDF"
        )
        if not folder:
            return

        paths = find_programacao_pdfs(folder)
        if not paths:
            QMessageBox.warning(
                self, "Comparar com PDFs", f"Nenhum PDF encontrado em:\n{folder}"
            )
            return

        try:
            voyages = parse_lanchas_pdfs(paths)
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao ler PDFs", str(exc))
            return

        if not voyages:
            QMessageBox.warning(
                self, "Comparar com PDFs",
                f"Nenhuma programação foi reconhecida nos {len(paths)} PDF(s) da pasta.",
            )
            return

        # A pasta pode conter dias diferentes — na de Downloads convivem as listas de 15 e
        # 16/08. Cruzar dias daria um relatório cheio de divergências falsas.
        data_planilha = self._dados_date(dados_rows)
        ignorados: list[str] = []
        if data_planilha is not None:
            do_dia = [v for v in voyages if v.data in (None, data_planilha)]
            ignorados = sorted({
                f"{v.source} ({v.data.strftime('%d/%m/%Y')})"
                for v in voyages if v.data is not None and v.data != data_planilha
            })
            if not do_dia:
                QMessageBox.warning(
                    self, "Comparar com PDFs",
                    f"Nenhum PDF da pasta é de {data_planilha.strftime('%d/%m/%Y')}, "
                    "a data da planilha Dados.\n\nIgnorados: " + ", ".join(ignorados),
                )
                return
            voyages = do_dia

        resultado = comparar(sheet_rows, voyages)

        # Processar sem salvar deixa a planilha do disco atrasada; o relatório seria sobre um
        # estado que o operador não reconhece.
        pendentes = sum(1 for fr in self._filled_rows if fr.status == "auto")
        nao_salvo = pendentes > 0 and len(sheet_rows) != pendentes

        dlg = QDialog(self)
        dlg.setWindowTitle("Comparação com as programações em PDF")
        dlg.setMinimumSize(820, 560)
        layout = QVBoxLayout(dlg)

        arquivos = sorted({v.source for v in voyages})
        cabecalho = f"{len(voyages)} viagens lidas em {len(arquivos)} PDF(s)"
        if data_planilha is not None:
            cabecalho += f" de {data_planilha.strftime('%d/%m/%Y')}"
        if resultado.iguais:
            cabecalho += " — tudo confere."
        resumo = QLabel(cabecalho)
        resumo.setStyleSheet(
            "color: #15803d; font-weight: bold;" if resultado.iguais
            else "color: #b91c1c; font-weight: bold;"
        )
        layout.addWidget(resumo)

        detalhe = QLabel(
            f"Comparando com a planilha gravada em disco ({len(sheet_rows)} movimentações "
            f"programadas).\nArquivos lidos: " + ", ".join(arquivos)
        )
        detalhe.setWordWrap(True)
        detalhe.setStyleSheet("color: #64748b;")
        layout.addWidget(detalhe)

        if nao_salvo:
            pendente = QLabel(
                f"A tabela desta sessão tem {pendentes} movimentações e a planilha em disco "
                f"tem {len(sheet_rows)}. Use \"Salvar na planilha\" antes de comparar, senão "
                "o relatório é sobre a versão anterior do arquivo."
            )
            pendente.setWordWrap(True)
            pendente.setStyleSheet("color: #b45309;")
            layout.addWidget(pendente)

        if ignorados:
            aviso = QLabel(
                "Ignorados por serem de outra data: " + ", ".join(ignorados)
            )
            aviso.setWordWrap(True)
            aviso.setStyleSheet("color: #b45309;")
            layout.addWidget(aviso)

        texto = QTextEdit()
        texto.setReadOnly(True)
        texto.setPlainText(formatar_relatorio(resultado))
        texto.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(texto)

        buttons = QHBoxLayout()
        buttons.addStretch()
        btn_close = QPushButton("Fechar")
        btn_close.clicked.connect(dlg.accept)
        buttons.addWidget(btn_close)
        layout.addLayout(buttons)
        dlg.exec()

    # ── Troca / permuta de viagem ──────────────────────────────────────
    def _linhas_selecionadas(self) -> list:
        """As FilledRow das linhas selecionadas, atravessando a ordenação da tabela.

        A tabela aceita Ctrl+clique e Shift+clique, então o operador seleciona um bloco de
        uma vez — foi o que resolveu os 71 pax do TMIB para o M9, em que a GAD determina os
        48 das duas primeiras lanchas e antes era um por um.
        """
        modelo = self._table.selectionModel()
        escolhidas = []
        for indice in (modelo.selectedRows() if modelo else []):
            item = self._table.item(indice.row(), 0)
            if item is None:
                continue
            idx = item.data(Qt.UserRole)
            if idx is None or idx >= len(self._visible_rows):
                continue
            escolhidas.append(self._visible_rows[idx])
        return escolhidas

    def _resolver(self):
        from .distribuicao import PLATFORM_EQUIVALENCES
        from .offshore_pd.aliases import AliasResolver
        return AliasResolver(explicit=dict(PLATFORM_EQUIVALENCES))

    @staticmethod
    def _viagem_txt(slot) -> str:
        hor = slot.horario.strftime("%H:%M") if slot and slot.horario else "--:--"
        return f"{slot.vessel} · {hor}" if slot else "sem viagem"

    @staticmethod
    def _sem_opcoes_txt(frs: list) -> str:
        """Por que não há para onde trocar — dizendo QUAL linha quebrou a seleção.

        "movimentações diferentes" não ajuda com 25 linhas marcadas: o operador não tem como
        achar a intrusa. Aconteceu de verdade — 24 pax `TMIB → PCM-9` e um `TMIB → PCB-1` que
        entrou junto no Shift+clique. Com a contagem por movimentação, a linha de 1 pax salta
        aos olhos.
        """
        movimentos: dict = {}
        for fr in frs:
            chave = (f"{fr.dados_row.origem_raw or '?'} → "
                     f"{fr.dados_row.destino_raw or '?'}")
            movimentos[chave] = movimentos.get(chave, 0) + 1

        if len(movimentos) == 1:
            return ("A operação não programou nenhuma outra viagem para "
                    + next(iter(movimentos)) + ".")

        linhas = [f"Não há nenhuma viagem programada que atenda os {len(frs)} "
                  f"selecionados de uma vez.", "", "A seleção tem movimentações diferentes:"]
        for chave, n in sorted(movimentos.items(), key=lambda kv: (-kv[1], kv[0])):
            linhas.append(f"    {chave}: {n} passageiro(s)")
        linhas += ["", "Cada viagem atende uma movimentação. Deixe selecionada só uma "
                       "delas — o filtro Destino ajuda a isolar o grupo."]
        return "\n".join(linhas)

    def _renumerar(self) -> None:
        """Refaz a numeração depois de mover alguém, e recarrega a tabela.

        A numeração conta as viagens que de fato levam pax de cada tipo, então o mapa que o
        `fill_rows` devolveu fica velho depois de uma troca. Reaproveitá-lo deixaria o
        Nº Viagem em branco — em silêncio — para quem entrou numa viagem que antes não
        levava ninguém do tipo dele.
        """
        from .distribuicao import PLATFORM_EQUIVALENCES
        from .distribuicao.filler import _assign_n_viagem, rebuild_n_viagem

        self._n_viagem_map = rebuild_n_viagem(
            self._filled_rows, self._trips, extra_aliases=PLATFORM_EQUIVALENCES)
        _assign_n_viagem(self._filled_rows, self._n_viagem_map)
        self._trocas_pendentes += 1
        self._populate_table()

    def _trocar_viagem(self) -> None:
        """Troca pareada: duas listas lado a lado, escolhendo pessoa em vez de viagem.

        O operador olha a lista do que selecionou e, para cada pax que não deveria estar ali,
        escolhe na segunda lista quem entra no lugar. A segunda lista é o agregado de todas
        as lanchas, que é o ponto: ele sabe o nome de quem quer trazer, não a lancha em que
        essa pessoa está — antes tinha de ir testando viagem por viagem.

        Como a troca é 1 por 1, nenhuma viagem estoura nem esvazia e não há permuta para
        negociar. O caminho por viagem continua acessível de dentro da janela, para o caso de
        mover alguém para uma viagem com vaga livre.
        """
        from .distribuicao.filler import apply_pairs, pair_swap_pool

        frs = self._linhas_selecionadas()
        if not frs:
            QMessageBox.information(
                self, "Trocar Viagem",
                "Selecione na tabela as linhas dos passageiros que vão mudar de viagem.\n"
                "Use Ctrl+clique para escolher vários e Shift+clique para um bloco.")
            return

        pool = pair_swap_pool(frs, self._filled_rows, self._trips,
                              resolver=self._resolver())
        if pool is None:
            QMessageBox.warning(
                self, "Trocar Viagem",
                "Não foi possível identificar origem e destino de alguma das linhas "
                "selecionadas, então não há como saber quais viagens as atendem.")
            return

        if not pool.candidatos:
            QMessageBox.information(
                self, "Trocar Viagem",
                "Não há nenhum passageiro em outra viagem que possa trocar de lugar com os "
                "selecionados.\n\nUse \"Mover para outra viagem\" se a intenção é levá-los "
                "para uma viagem com vaga livre.")
            self._mover_para_viagem(frs)
            return

        dlg = _TrocaParesDialog(frs, pool, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        if dlg.modo == _TrocaParesDialog._MODO_VIAGEM:
            self._mover_para_viagem(frs)
            return

        pares = dlg.pares()
        if not pares:
            return

        apply_pairs(pares, pool)
        self._renumerar()

        linhas = [f"{sai.dados_row.name or '?'}  ⇄  {entra.dados_row.name or '?'}"
                  for sai, entra in pares[:12]]
        if len(pares) > 12:
            linhas.append(f"... e mais {len(pares) - 12}")
        QMessageBox.information(
            self, "Trocar Viagem",
            f"{len(pares)} troca(s) aplicada(s):\n\n" + "\n".join(linhas) +
            "\n\nLembre de salvar na planilha: a troca vale só nesta sessão.")

    def _mover_para_viagem(self, frs: list) -> None:
        """Caminho por viagem: move os pax selecionados para outra viagem programada.

        Deixou de ser o caminho principal — virou o botao "Mover para outra viagem..." de
        dentro do `_TrocaParesDialog` — porque exige que o operador saiba de antemao em qual
        viagem esta quem ele quer trazer. Continua sendo o unico caminho para mover alguem
        para uma viagem com **vaga livre**, em que nao ha par para montar.

        Quando a viagem de destino não tem vaga para todos, a diferença sai por permuta: quem
        sai assume as vagas que o lote está liberando, então nenhuma viagem estoura nem
        esvazia. A decisão de quais viagens servem, quem está em cada uma, quem pode ceder o
        lugar e para onde cada um vai fica toda no `filler` — aqui só entra a conversa.
        """
        from .distribuicao.filler import (
            batch_swap_candidates, batch_swap_options, match_displaced, swap_vacancies,
        )

        resolver = self._resolver()
        resultado = batch_swap_options(frs, self._filled_rows, self._trips,
                                       resolver=resolver)
        if resultado is None:
            QMessageBox.warning(
                self, "Trocar Viagem",
                "Não foi possível identificar origem e destino de alguma das linhas "
                "selecionadas, então não há como saber quais viagens as atendem.")
            return

        atuais, opcoes = resultado
        if not opcoes:
            QMessageBox.information(self, "Trocar Viagem", self._sem_opcoes_txt(frs))
            return

        dlg = _TrocarViagemDialog(frs, atuais, opcoes, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        escolha = dlg.escolhido()
        if escolha is None:
            QMessageBox.warning(self, "Trocar Viagem", "Nenhuma viagem foi selecionada.")
            return

        destino, ocupados = escolha
        # Quem já está na viagem de destino não entra na conta.
        entram = [fr for fr in frs
                  if not (atuais.get(id(fr)) and
                          (atuais[id(fr)].vessel, atuais[id(fr)].horario)
                          == (destino.vessel, destino.horario))]
        if not entram:
            QMessageBox.information(
                self, "Trocar Viagem", "Os selecionados já estão nessa viagem.")
            return

        livre = max(0, destino.limit - ocupados)
        precisa = max(0, len(entram) - livre)
        vagas = swap_vacancies(entram, atuais, destino)
        saindo: list = []
        destino_de: dict = {}

        if precisa:
            candidatos = batch_swap_candidates(
                entram, vagas, destino, self._filled_rows, resolver=resolver)
            if len(candidatos) < precisa:
                QMessageBox.warning(
                    self, "Trocar Viagem",
                    f"A viagem tem {livre} vaga(s) para {len(entram)} passageiro(s), então "
                    f"{precisa} teriam de sair — mas só {len(candidatos)} dos que estão "
                    f"nela podem assumir as vagas que o lote libera. A troca não fecha.")
                return

            vagas_txt = ", ".join(sorted(
                f"{self._viagem_txt(v)}" for v in {(v.vessel, v.horario): v
                                                   for v in vagas}.values()))
            perm = _PermutaDialog(
                entram, self._viagem_txt(destino), vagas_txt, precisa, candidatos,
                sem_vaga=max(0, precisa - len(vagas)), parent=self)
            if perm.exec() != QDialog.Accepted:
                return
            saindo = perm.escolhidos()
            if len(saindo) != precisa:
                QMessageBox.warning(
                    self, "Trocar Viagem",
                    f"É preciso escolher exatamente {precisa} passageiro(s) para sair; "
                    f"você marcou {len(saindo)}.")
                return

            destino_de = match_displaced(saindo, vagas, resolver=resolver) or {}
            if not destino_de:
                QMessageBox.warning(
                    self, "Trocar Viagem",
                    "Não há como distribuir quem sai pelas vagas liberadas respeitando a "
                    "programação da operação. Escolha outros passageiros para sair.")
                return

        for fr in entram:
            fr.embarcacao = destino.vessel
            fr.horario = destino.horario
            fr.status = "auto"

        for fr in saindo:
            vaga = destino_de.get(id(fr))
            if vaga is None:
                fr.embarcacao = None
                fr.horario = None
                fr.n_viagem = None
                fr.status = "sob_demanda"
            else:
                fr.embarcacao = vaga.vessel
                fr.horario = vaga.horario
                fr.status = "auto"

        self._renumerar()

        linhas = [f"{len(entram)} passageiro(s) → {self._viagem_txt(destino)}"]
        if saindo:
            por_vaga: dict = {}
            for fr in saindo:
                por_vaga.setdefault(self._viagem_txt(destino_de.get(id(fr))), 0)
                por_vaga[self._viagem_txt(destino_de.get(id(fr)))] += 1
            for txt, n in sorted(por_vaga.items()):
                linhas.append(f"{n} passageiro(s) → {txt}")
        QMessageBox.information(
            self, "Trocar Viagem",
            "\n".join(linhas) +
            "\n\nLembre de salvar na planilha: a troca vale só nesta sessão.")

    def _adicionar_trecho(self) -> None:
        sob_demanda = [fr for fr in self._filled_rows if fr.status == "sob_demanda"]
        if not sob_demanda:
            QMessageBox.information(self, "Adicionar Trecho", "Não há passageiros sob demanda.")
            return

        dlg = _AddTrechoDialog(sob_demanda, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        vessel, horario_str, selected_rows = dlg.get_selection()
        if not vessel or not selected_rows:
            QMessageBox.warning(self, "Adicionar Trecho", "Informe a embarcação e selecione ao menos um passageiro.")
            return

        from datetime import time as dt_time
        try:
            h, m = horario_str.split(":")
            horario = dt_time(int(h), int(m))
        except (ValueError, AttributeError):
            QMessageBox.warning(
                self, "Adicionar Trecho",
                f"Horário inválido: {horario_str!r}. Use o formato HH:MM.",
            )
            return

        for fr in selected_rows:
            fr.embarcacao = vessel
            fr.horario = horario
            fr.status = "auto"

        # Without this the rows keep embarcacao/horario but no Nº VIAGEM.
        from .distribuicao.filler import _assign_n_viagem
        _assign_n_viagem(self._filled_rows, self._n_viagem_map)

        self._populate_table()


class PassengerManifestTab(QWidget):
    def __init__(self, service: AppService, parent_window: "MainWindow"):
        super().__init__()
        self.service = service
        self.parent_window = parent_window
        self.pdf_dir_edit = QLineEdit()
        self.route_table = QTableWidget(0, 6)
        self._pax_snapshot: dict[str, dict[str, int]] = {}
        self._user_overridden_limits: set[int] = set()
        self._updating_route_calc = False
        self.transfers_table = QTableWidget(0, 6)
        self.result_table = QTableWidget(0, 6)
        self.vessel_filter_combo = QComboBox()
        self.issues_text = QTextEdit()
        self.summary_text = QTextEdit()
        self._loaded_deliveries: List[DeliveryRecord] = []
        self._loaded_transfers: List[TransferRecord] = []
        self._read_errors: List[str] = []
        self._latest_result: Optional[PickupListResult] = None
        self._current_assignments: Optional[List[PassengerAssignment]] = None
        self._updating_manifest_tables = False
        self._build()

    def _build(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer_layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)

        intro = QLabel(
            "Abra a pasta com os PDFs, confirme os transbordos realizados e monte o roteiro de recolhimento por parada."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #475569;")
        layout.addWidget(intro)

        folder_box = QGroupBox("Arquivos de entrada")
        folder_layout = QGridLayout(folder_box)
        self.pdf_dir_edit.setPlaceholderText("Pasta com os PDFs")
        browse_btn = QPushButton("Procurar pasta")
        browse_btn.clicked.connect(self._choose_pdf_folder)
        read_pdfs_btn = QPushButton("Ler PDFs")
        read_pdfs_btn.clicked.connect(self.load_pdf_records)
        folder_layout.addWidget(QLabel("Pasta dos PDFs:"), 0, 0)
        folder_layout.addWidget(self.pdf_dir_edit, 0, 1)
        folder_layout.addWidget(browse_btn, 0, 2)
        folder_layout.addWidget(read_pdfs_btn, 0, 3)
        layout.addWidget(folder_box)

        route_box = QGroupBox("Roteiro de recolhimento")
        route_layout = QVBoxLayout(route_box)
        route_help = QLabel(
            "Uma linha por parada. Em 'Destinos a recolher', use TODOS ou informe TMIB, M9, M1 separados por virgula. "
            "Ex.: B1/TMIB para uma lancha e B1/M9 para outra."
        )
        route_help.setWordWrap(True)
        route_help.setStyleSheet("color: #475569;")
        route_layout.addWidget(route_help)
        self.route_table.setHorizontalHeaderLabels(
            ["Embarcacao", "Parada", "Destinos a recolher", "Pax", "A recolher", "Bordo"]
        )
        self.route_table.setMinimumHeight(190)
        self.route_table.installEventFilter(self)
        self.route_table.itemChanged.connect(self._on_route_table_item_changed)
        route_header = self.route_table.horizontalHeader()
        route_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        route_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        route_header.setSectionResizeMode(2, QHeaderView.Stretch)
        route_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        route_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        route_header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        route_layout.addWidget(self.route_table)
        route_buttons = QHBoxLayout()
        add_stop_btn = QPushButton("Adicionar parada")
        add_stop_btn.clicked.connect(self.add_route_stop)
        remove_stop_btn = QPushButton("Excluir parada")
        remove_stop_btn.clicked.connect(self.remove_selected_route_stop)
        up_btn = QPushButton("Subir")
        up_btn.clicked.connect(lambda: self.move_selected_route_stop(-1))
        down_btn = QPushButton("Descer")
        down_btn.clicked.connect(lambda: self.move_selected_route_stop(1))
        import_route_btn = QPushButton("Importar roteiro TXT")
        import_route_btn.clicked.connect(self._load_route_file)
        route_buttons.addWidget(add_stop_btn)
        route_buttons.addWidget(remove_stop_btn)
        route_buttons.addWidget(up_btn)
        route_buttons.addWidget(down_btn)
        route_buttons.addWidget(import_route_btn)
        route_buttons.addStretch(1)
        route_layout.addLayout(route_buttons)
        layout.addWidget(route_box)

        transfers_box = QGroupBox("Transbordos lidos dos PDFs")
        transfers_layout = QVBoxLayout(transfers_box)
        transfers_help = QLabel(
            "Marque SIM para aplicar o transbordo no razao de passageiros. Troque para NAO se o transbordo planejado nao aconteceu."
        )
        transfers_help.setWordWrap(True)
        transfers_help.setStyleSheet("color: #475569;")
        transfers_layout.addWidget(transfers_help)
        self.transfers_table.setHorizontalHeaderLabels(["Realizado", "Passageiro", "De", "Para", "Horario", "Arquivo"])
        self.transfers_table.setMinimumHeight(170)
        self.transfers_table.itemChanged.connect(self._on_transfer_confirmation_changed)
        transfer_header = self.transfers_table.horizontalHeader()
        transfer_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        transfer_header.setSectionResizeMode(1, QHeaderView.Stretch)
        transfer_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        transfer_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        transfer_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        transfer_header.setSectionResizeMode(5, QHeaderView.Stretch)
        transfers_layout.addWidget(self.transfers_table)
        layout.addWidget(transfers_box)

        action_row = QHBoxLayout()
        generate_btn = QPushButton("Gerar lista")
        generate_btn.clicked.connect(self.generate_manifest_list)
        export_btn = QPushButton("Exportar CSV")
        export_btn.clicked.connect(self.export_csv)
        print_btn = QPushButton("Gerar impressao")
        print_btn.clicked.connect(self.export_printable_manifest)
        clear_btn = QPushButton("Limpar")
        clear_btn.clicked.connect(self.clear_output)
        action_row.addWidget(generate_btn)
        action_row.addWidget(export_btn)
        action_row.addWidget(print_btn)
        action_row.addWidget(clear_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(USE_COLLAPSIBLE_SPLITTERS)
        splitter.setMinimumHeight(420)

        table_box = QGroupBox("Lista por embarcacao")
        table_layout = QVBoxLayout(table_box)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filtrar embarcacao:"))
        self.vessel_filter_combo.addItem("TODAS")
        self.vessel_filter_combo.currentTextChanged.connect(self._apply_result_filter)
        filter_row.addWidget(self.vessel_filter_combo)
        add_pax_btn = QPushButton("+ Incluir pax")
        add_pax_btn.setToolTip("Adicionar passageiro manualmente a uma embarcacao")
        add_pax_btn.clicked.connect(self._on_add_pax_clicked)
        filter_row.addWidget(add_pax_btn)
        filter_row.addStretch(1)
        table_layout.addLayout(filter_row)
        self.result_table.setHorizontalHeaderLabels(
            ["Embarcacao", "Plataforma", "Destino", "Passageiro", "Documento", "Fontes"]
        )
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_table.cellClicked.connect(self._on_result_pax_cell_clicked)
        self.result_table.setMinimumHeight(320)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        table_layout.addWidget(self.result_table)
        splitter.addWidget(table_box)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        summary_box = QGroupBox("Resumo")
        summary_layout = QVBoxLayout(summary_box)
        self.summary_text.setReadOnly(True)
        self.summary_text.setMinimumHeight(130)
        summary_layout.addWidget(self.summary_text)
        right_layout.addWidget(summary_box)

        issues_box = QGroupBox("Pendencias e alertas")
        issues_layout = QVBoxLayout(issues_box)
        self.issues_text.setReadOnly(True)
        self.issues_text.setMinimumHeight(160)
        issues_layout.addWidget(self.issues_text)
        right_layout.addWidget(issues_box)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

    def _choose_pdf_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta dos PDFs")
        if folder:
            self.pdf_dir_edit.setText(folder)

    def _load_route_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Selecionar roteiro CL", filter="TXT (*.txt);;Todos (*.*)")
        if not file_name:
            return
        try:
            text = Path(file_name).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = Path(file_name).read_text(encoding="latin1")
        itineraries = parse_cl_itinerary_text(text)
        if not itineraries:
            itineraries = self._parse_compact_manifest_routes(text)
        if not itineraries:
            QMessageBox.warning(self, "Manifestos", "Nao foi possivel identificar embarcacoes no roteiro.")
            return
        self.route_table.setRowCount(0)
        for itinerary in itineraries:
            for stop in itinerary.stops:
                destinations = itinerary.pickup_filters.get(stop, ["TODOS"])
                limit = itinerary.pickup_limits.get(stop)
                self._append_route_stop(
                    itinerary.vessel,
                    stop,
                    ",".join(destinations) if destinations else "TODOS",
                    str(limit) if limit is not None else "",
                )

    @staticmethod
    def _parse_compact_manifest_routes(text: str) -> List[VesselItinerary]:
        itineraries: List[VesselItinerary] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or ">" not in line:
                continue

            match = re.match(r"^(?P<vessel>(?:SURFER\s*)?\d{4}|[A-Z][A-Z0-9 ]*?)\s+(?P<route>.+)$", line, re.IGNORECASE)
            if not match:
                continue
            vessel = re.sub(r"\s+", " ", match.group("vessel").strip().upper())
            if re.fullmatch(r"\d{4}", vessel):
                vessel = f"SURFER {vessel}"
            route_text = match.group("route").strip()

            stops: List[str] = []
            filters: dict[str, List[str]] = {}
            limits: dict[str, int] = {}
            for token in route_text.split(">"):
                token = token.strip()
                if not token:
                    continue
                stop_match = re.match(r"^(?P<stop>[A-Z0-9\-]+)\s*(?:\((?P<destinations>[^)]*)\))?$", token, re.IGNORECASE)
                if not stop_match:
                    continue
                stop = stop_match.group("stop").strip().upper()
                destinations_text = (stop_match.group("destinations") or "").strip()
                stops.append(stop)
                if destinations_text:
                    destinations = []
                    stop_limit: int | None = None
                    for item in re.split(r"[,;/\s]+", destinations_text):
                        item = item.strip().upper()
                        if not item:
                            continue
                        # support DEST:N notation (e.g. TMIB:8)
                        if ":" in item:
                            dest_part, _, limit_part = item.partition(":")
                            destinations.append(dest_part)
                            try:
                                parsed = int(limit_part)
                                if parsed > 0:
                                    stop_limit = parsed
                            except ValueError:
                                pass
                        else:
                            destinations.append(item)
                    if destinations:
                        filters[stop] = destinations
                    if stop_limit is not None:
                        limits[stop] = stop_limit
            if stops:
                itineraries.append(VesselItinerary(vessel=vessel, stops=stops, pickup_filters=filters, pickup_limits=limits))
        return itineraries

    def add_route_stop(self) -> None:
        selected = self.route_table.currentRow()
        vessel = ""
        if selected >= 0:
            vessel = self._route_vessel_at_row(selected)
        self._append_route_stop(vessel, "", "TODOS")

    def _append_route_stop(self, vessel: str, stop: str, destinations: str, limit: str = "") -> None:
        row = self.route_table.rowCount()
        self._insert_route_stop(row, vessel, stop, destinations, limit)

    def _insert_route_stop(self, row: int, vessel: str, stop: str, destinations: str, limit: str = "") -> None:
        self._updating_route_calc = True
        try:
            self.route_table.insertRow(row)
            self._set_route_vessel_cell(row, vessel)
            self._set_route_stop_cell(row, stop)
            self._set_route_destinations_cell(row, destinations or "TODOS")
            pax_item = QTableWidgetItem("")
            pax_item.setFlags(pax_item.flags() & ~Qt.ItemIsEditable)
            pax_item.setTextAlignment(Qt.AlignCenter)
            self.route_table.setItem(row, 3, pax_item)
            a_recolher_item = QTableWidgetItem(limit)
            a_recolher_item.setTextAlignment(Qt.AlignCenter)
            self.route_table.setItem(row, 4, a_recolher_item)
            bordo_item = QTableWidgetItem("")
            bordo_item.setFlags(bordo_item.flags() & ~Qt.ItemIsEditable)
            bordo_item.setTextAlignment(Qt.AlignCenter)
            self.route_table.setItem(row, 5, bordo_item)
            self.route_table.setCurrentCell(row, 1)
        finally:
            self._updating_route_calc = False
        if limit:
            self._user_overridden_limits.add(row)
        self._recalculate_route_rows_from(row)

    def _available_manifest_vessels(self) -> List[str]:
        op_config = self.parent_window.current_op_config
        if not op_config:
            return []
        return [vessel.nome for vessel in op_config.frota if vessel.ativa]

    def _set_route_vessel_cell(self, row: int, selected_vessel: str = "") -> None:
        combo = QComboBox()
        combo.setEditable(True)
        combo.installEventFilter(self)
        vessels = self._available_manifest_vessels()
        if selected_vessel and selected_vessel not in vessels:
            vessels = [selected_vessel] + vessels
        combo.addItems(vessels)
        if selected_vessel:
            index = combo.findText(selected_vessel)
            if index >= 0:
                combo.setCurrentIndex(index)
        if combo.lineEdit():
            combo.lineEdit().installEventFilter(self)
        combo.currentTextChanged.connect(self._on_vessel_combo_changed)
        self.route_table.setCellWidget(row, 0, combo)
        self.route_table.setItem(row, 0, QTableWidgetItem(combo.currentText()))

    def _route_vessel_at_row(self, row: int) -> str:
        widget = self.route_table.cellWidget(row, 0)
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()
        item = self.route_table.item(row, 0)
        return item.text().strip() if item else ""

    def _set_route_stop_cell(self, row: int, selected_stop: str = "") -> None:
        combo = QComboBox()
        combo.setEditable(True)
        combo.installEventFilter(self)
        stops = self._available_manifest_stops()
        if selected_stop and selected_stop not in stops:
            stops = [selected_stop] + stops
        combo.addItems(stops)
        if selected_stop:
            index = combo.findText(selected_stop)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.currentTextChanged.connect(self._on_stop_combo_changed)
        if combo.lineEdit():
            combo.lineEdit().installEventFilter(self)
        self.route_table.setCellWidget(row, 1, combo)
        self.route_table.setItem(row, 1, QTableWidgetItem(combo.currentText()))

    def _route_stop_at_row(self, row: int) -> str:
        widget = self.route_table.cellWidget(row, 1)
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()
        item = self.route_table.item(row, 1)
        return item.text().strip() if item else ""

    def _set_route_destinations_cell(self, row: int, selected_destinations: str = "TODOS") -> None:
        combo = QComboBox()
        combo.installEventFilter(self)
        options = self._available_destination_options_for_stop(self._route_stop_at_row(row))
        selected = selected_destinations.strip().upper() if selected_destinations else "TODOS"
        if selected not in options:
            selected = "TODOS"
        combo.addItems(options)
        index = combo.findText(selected)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.currentTextChanged.connect(self._on_destination_combo_changed)
        self.route_table.setCellWidget(row, 2, combo)
        self.route_table.setItem(row, 2, QTableWidgetItem(combo.currentText()))

    def eventFilter(self, source, event):  # type: ignore[override]
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._is_route_table_editor(source):
                self._add_route_stop_below_current()
                return True
        return super().eventFilter(source, event)

    def _is_route_table_editor(self, source: object) -> bool:
        if source is self.route_table:
            return True
        for row in range(self.route_table.rowCount()):
            for col in range(self.route_table.columnCount()):
                widget = self.route_table.cellWidget(row, col)
                if source is widget:
                    return True
                if isinstance(widget, QComboBox) and widget.lineEdit() and source is widget.lineEdit():
                    return True
        return False

    def _add_route_stop_below_current(self) -> None:
        row = self.route_table.currentRow()
        if row < 0:
            row = self.route_table.rowCount() - 1
        vessel = self._route_vessel_at_row(row) if row >= 0 else ""
        insert_at = max(row + 1, 0)
        self._insert_route_stop(insert_at, vessel, "", "TODOS")
        stop_widget = self.route_table.cellWidget(insert_at, 1)
        if stop_widget:
            stop_widget.setFocus()

    def _route_destinations_at_row(self, row: int) -> str:
        widget = self.route_table.cellWidget(row, 2)
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()
        item = self.route_table.item(row, 2)
        return item.text().strip() if item else ""

    def _route_limit_at_row(self, row: int) -> int | None:
        item = self.route_table.item(row, 4)
        text = item.text().strip() if item else ""
        try:
            value = int(text)
            return value if value > 0 else None
        except ValueError:
            return None

    def _on_route_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_route_calc:
            return
        if item.column() == 4:
            self._user_overridden_limits.add(item.row())
            self._recalculate_route_rows_from(item.row() + 1)

    def _on_vessel_combo_changed(self, _text: str) -> None:
        sender = self.sender()
        if sender is None:
            return
        for row in range(self.route_table.rowCount()):
            if self.route_table.cellWidget(row, 0) is sender:
                self._on_route_context_changed(row)
                return

    def _on_stop_combo_changed(self, _text: str) -> None:
        sender = self.sender()
        if sender is None:
            return
        for row in range(self.route_table.rowCount()):
            if self.route_table.cellWidget(row, 1) is sender:
                self._on_route_stop_changed(row)
                return

    def _on_destination_combo_changed(self, _text: str) -> None:
        sender = self.sender()
        if sender is None:
            return
        for row in range(self.route_table.rowCount()):
            if self.route_table.cellWidget(row, 2) is sender:
                self._on_route_context_changed(row)
                return

    def _on_route_context_changed(self, row: int) -> None:
        if self._updating_route_calc:
            return
        self._user_overridden_limits.discard(row)
        self._recalculate_route_rows_from(row)

    def _build_pax_snapshot(self) -> None:
        if not self._loaded_deliveries and not self._loaded_transfers:
            self._pax_snapshot = {}
            return
        alias = AliasResolver()
        confirmed_transfers = self._confirmed_transfers()
        ledger = build_passenger_positions(
            self._loaded_deliveries,
            confirmed_transfers,
            self._configured_return_origins(),
        )
        snapshot: dict[str, dict[str, int]] = {}
        for entry in ledger.values():
            plat = entry.current_platform
            dest = entry.return_destination
            if plat == dest or plat == "TMIB":
                continue
            snapshot.setdefault(plat, {})
            snapshot[plat][dest] = snapshot[plat].get(dest, 0) + 1
            snapshot[plat]["TODOS"] = snapshot[plat].get("TODOS", 0) + 1

        # The UI destination options include direct transfer-origin evidence:
        # e.g. M1 -> M4 means there are M1-return pax waiting at M4. Keep the
        # Pax count aligned with those options even when the delivery manifest
        # match is imperfect or the passenger entered the ledger only via a
        # transfer document.
        return_origins = self._configured_return_origins(alias)
        direct_transfer_counts: dict[str, dict[str, int]] = {}
        for transfer in confirmed_transfers:
            from_platform = alias.operational(transfer.from_platform)
            to_platform = alias.operational(transfer.to_platform)
            if from_platform not in return_origins or not to_platform or to_platform == from_platform:
                continue
            direct_transfer_counts.setdefault(to_platform, {})
            direct_transfer_counts[to_platform][from_platform] = (
                direct_transfer_counts[to_platform].get(from_platform, 0) + 1
            )

        for platform, by_destination in direct_transfer_counts.items():
            snapshot.setdefault(platform, {})
            for destination, count in by_destination.items():
                existing = snapshot[platform].get(destination, 0)
                if count <= existing:
                    continue
                delta = count - existing
                snapshot[platform][destination] = count
                snapshot[platform]["TODOS"] = snapshot[platform].get("TODOS", 0) + delta
        self._pax_snapshot = snapshot

    def _count_pax_at_stop(self, stop: str, destination_text: str) -> int:
        plat_data = self._pax_snapshot.get(stop.upper(), {})
        if not destination_text or destination_text.upper() in {"TODOS", "TODO", "ALL", "*"}:
            return plat_data.get("TODOS", 0)
        total = 0
        for dest in re.split(r"[,;/\s]+", destination_text.upper()):
            dest = dest.strip()
            if dest:
                total += plat_data.get(dest, 0)
        return total

    def _sum_a_recolher_at_stop(self, up_to_row: int, stop: str, destination_text: str) -> int:
        stop_upper = stop.upper()
        dest_is_todos = not destination_text or destination_text.upper() in {"TODOS", "TODO", "ALL", "*"}
        dest_set = (
            {d.strip() for d in re.split(r"[,;/\s]+", destination_text.upper()) if d.strip()}
            if not dest_is_todos else set()
        )
        total = 0
        for r in range(up_to_row):
            if self._route_stop_at_row(r).upper() != stop_upper:
                continue
            row_dest = self._route_destinations_at_row(r).upper()
            row_dest_is_todos = not row_dest or row_dest in {"TODOS", "TODO", "ALL", "*"}
            if dest_is_todos or row_dest_is_todos:
                overlap = True
            else:
                row_dest_set = {d.strip() for d in re.split(r"[,;/\s]+", row_dest) if d.strip()}
                overlap = bool(dest_set & row_dest_set)
            if overlap:
                total += self._route_limit_at_row(r) or 0
        return total

    def _sum_a_recolher_for_vessel(self, up_to_row: int, vessel: str) -> int:
        vessel_upper = vessel.upper()
        return sum(
            self._route_limit_at_row(r) or 0
            for r in range(up_to_row)
            if self._route_vessel_at_row(r).upper() == vessel_upper
        )

    def _manifest_vessel_capacity_single(self, vessel: str) -> int:
        return self._manifest_vessel_capacities().get(vessel, 24)

    def _pax_on_board_arriving_at_row(self, target_row: int, vessel: str) -> int:
        """Pax on board when arriving at target_row's stop (before new pickup there).

        For rows with a single explicit destination: exact disembarkation.
        For rows with TODOS/empty destination: uses the pax snapshot to estimate
        the proportion of pax that will disembark at each stop along the route.
        """
        vessel_upper = vessel.upper()
        # First visit row index for each stop name
        first_visit_row: dict[str, int] = {}
        for r in range(self.route_table.rowCount()):
            if self._route_vessel_at_row(r).upper() != vessel_upper:
                continue
            s = self._route_stop_at_row(r).upper()
            if s not in first_visit_row:
                first_visit_row[s] = r

        bordo = 0.0
        for r in range(target_row):
            if self._route_vessel_at_row(r).upper() != vessel_upper:
                continue
            dest = self._route_destinations_at_row(r).upper()
            dest_tokens = [
                t.strip() for t in re.split(r"[,;/\s]+", dest)
                if t.strip() and t.strip() not in {"TODOS", "TODO", "ALL", "*"}
            ]
            a = float(self._route_limit_at_row(r) or 0)

            if len(dest_tokens) == 1:
                # Single explicit destination — exact calculation
                disembark_row = first_visit_row.get(dest_tokens[0], 99999)
                if disembark_row <= target_row:
                    continue  # disembarked before or at this stop
                bordo += a
            elif not dest_tokens:
                # TODOS / empty — use snapshot for proportional estimation
                stop_r = self._route_stop_at_row(r).upper()
                snap = self._pax_snapshot.get(stop_r, {})
                total_snap = snap.get("TODOS", 0)
                if total_snap > 0 and a > 0:
                    still_on_board = 0.0
                    for dest_d, count_d in snap.items():
                        if dest_d == "TODOS":
                            continue
                        proportion = count_d / total_snap
                        disembark_row_d = first_visit_row.get(dest_d, 99999)
                        if disembark_row_d > target_row:
                            still_on_board += proportion * a
                    bordo += still_on_board
                else:
                    bordo += a  # no snapshot data — conservative
            else:
                # Multiple explicit destinations — conservative, keep all on board
                bordo += a

        return max(0, round(bordo))

    def _recalculate_route_rows_from(self, start_row: int) -> None:
        for row in range(start_row, self.route_table.rowCount()):
            self._recalculate_route_row(row)

    def _recalculate_route_row(self, row: int) -> None:
        stop = self._route_stop_at_row(row)
        destination_text = self._route_destinations_at_row(row).upper()
        vessel = self._route_vessel_at_row(row)

        self._updating_route_calc = True
        try:
            pax_item = self.route_table.item(row, 3)
            if pax_item is None:
                pax_item = QTableWidgetItem()
                pax_item.setFlags(pax_item.flags() & ~Qt.ItemIsEditable)
                pax_item.setTextAlignment(Qt.AlignCenter)
                self.route_table.setItem(row, 3, pax_item)

            bordo_item = self.route_table.item(row, 5)
            if bordo_item is None:
                bordo_item = QTableWidgetItem()
                bordo_item.setFlags(bordo_item.flags() & ~Qt.ItemIsEditable)
                bordo_item.setTextAlignment(Qt.AlignCenter)
                self.route_table.setItem(row, 5, bordo_item)

            if not stop or not vessel or not self._pax_snapshot:
                pax_item.setText("")
                bordo_item.setText("")
                return

            pax_at_stop = self._count_pax_at_stop(stop, destination_text)
            already_assigned = self._sum_a_recolher_at_stop(row, stop, destination_text)
            available = max(0, pax_at_stop - already_assigned)

            vessel_capacity = self._manifest_vessel_capacity_single(vessel)
            # Fix: use arriving pax count (accounts for intermediate disembarkations)
            pax_on_board = self._pax_on_board_arriving_at_row(row, vessel)
            vessel_remaining = max(0, vessel_capacity - pax_on_board)

            a_recolher = min(vessel_remaining, available)
            pax_item.setText(str(pax_at_stop))

            if row not in self._user_overridden_limits:
                limit_item = self.route_table.item(row, 4)
                if limit_item is None:
                    limit_item = QTableWidgetItem()
                    limit_item.setTextAlignment(Qt.AlignCenter)
                    self.route_table.setItem(row, 4, limit_item)
                limit_item.setText(str(a_recolher))

            # Bordo = pax on board after pickup at this stop
            a_recolher_final = (
                a_recolher if row not in self._user_overridden_limits
                else (self._route_limit_at_row(row) or 0)
            )
            bordo_after = pax_on_board + a_recolher_final
            bordo_item.setText(f"{bordo_after}/{vessel_capacity}")
        finally:
            self._updating_route_calc = False

    def _on_route_stop_changed(self, row: int) -> None:
        if row < 0 or row >= self.route_table.rowCount():
            return
        self._updating_route_calc = True
        try:
            stop = self._route_stop_at_row(row)
            self.route_table.setItem(row, 1, QTableWidgetItem(stop))
            current_destinations = self._route_destinations_at_row(row)
            self._set_route_destinations_cell(row, current_destinations or "TODOS")
        finally:
            self._updating_route_calc = False
        self._on_route_context_changed(row)

    def _on_transfer_confirmation_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_manifest_tables or item.column() != 0:
            return
        self._build_pax_snapshot()
        self._refresh_route_stop_and_destination_options()
        self._recalculate_route_rows_from(0)

    def _available_manifest_stops(self) -> List[str]:
        return sorted(self._manifest_destinations_by_platform().keys())

    def _available_destination_options_for_stop(self, stop: str) -> List[str]:
        destinations = self._manifest_destinations_by_platform().get(stop, [])
        if not destinations:
            return ["TODOS"]
        options = ["TODOS"]
        for size in range(1, len(destinations)):
            for group in combinations(destinations, size):
                options.append(",".join(group))
        return options

    def _manifest_destinations_by_platform(self) -> dict[str, List[str]]:
        alias = AliasResolver()
        ledger: dict[str, tuple[str, str]] = {}
        name_index: dict[str, str] = {}
        confirmed_transfers = self._confirmed_transfers()
        for record in self._loaded_deliveries:
            key = self._passenger_key(record.passenger_name, record.passenger_id)
            ledger[key] = (
                alias.operational(record.current_platform),
                alias.operational(record.return_destination),
            )
            name_index[self._passenger_name_key(record.passenger_name)] = key

        for transfer in confirmed_transfers:
            key = self._passenger_key(transfer.passenger_name, transfer.passenger_id)
            if key not in ledger:
                key = name_index.get(self._passenger_name_key(transfer.passenger_name), key)
            if key in ledger:
                _platform, destination = ledger[key]
                from_platform = alias.operational(transfer.from_platform)
                if from_platform in self._configured_return_origins(alias):
                    destination = from_platform
                ledger[key] = (alias.operational(transfer.to_platform), destination)
            else:
                ledger[key] = (
                    alias.operational(transfer.to_platform),
                    alias.operational(transfer.from_platform),
                )
                name_index.setdefault(self._passenger_name_key(transfer.passenger_name), key)

        destinations_by_platform: dict[str, set[str]] = {}
        for platform, destination in ledger.values():
            if not platform:
                continue
            destinations_by_platform.setdefault(platform, set()).add(destination)
        return_origins = self._configured_return_origins(alias)
        for transfer in confirmed_transfers:
            from_platform = alias.operational(transfer.from_platform)
            to_platform = alias.operational(transfer.to_platform)
            if from_platform in return_origins:
                destinations_by_platform.setdefault(to_platform, set()).add(from_platform)
        return {
            platform: sorted(destinations)
            for platform, destinations in destinations_by_platform.items()
        }

    def _configured_return_origins(self, alias: AliasResolver | None = None) -> set[str]:
        resolver = alias or AliasResolver()
        op_config = self.parent_window.current_op_config
        origins: set[str] = {"TMIB", "M9", "M1"}
        for item in getattr(op_config, "origens_extrato", []) or []:
            if getattr(item, "ativa", True):
                code = str(getattr(item, "codigo", "")).strip()
                if code:
                    origins.add(resolver.operational(code))
        return origins

    @staticmethod
    def _passenger_key(passenger_name: str, passenger_id: str = "") -> str:
        if passenger_id.strip():
            return f"id:{passenger_id.strip().upper()}"
        return f"name:{passenger_name.strip().upper()}"

    @staticmethod
    def _passenger_name_key(passenger_name: str) -> str:
        text = unicodedata.normalize("NFKD", passenger_name)
        text = "".join(char for char in text if not unicodedata.combining(char))
        text = re.sub(r"[^A-Z0-9]+", " ", text.upper())
        return re.sub(r"\s+", " ", text).strip()

    def _refresh_route_stop_and_destination_options(self) -> None:
        for row in range(self.route_table.rowCount()):
            vessel = self._route_vessel_at_row(row)
            stop = self._route_stop_at_row(row)
            destinations = self._route_destinations_at_row(row)
            self._set_route_vessel_cell(row, vessel)
            self._set_route_stop_cell(row, stop)
            self._set_route_destinations_cell(row, destinations or "TODOS")

    def remove_selected_route_stop(self) -> None:
        rows = sorted({index.row() for index in self.route_table.selectedIndexes()}, reverse=True)
        if not rows and self.route_table.currentRow() >= 0:
            rows = [self.route_table.currentRow()]
        for row in rows:
            self.route_table.removeRow(row)

    def move_selected_route_stop(self, direction: int) -> None:
        row = self.route_table.currentRow()
        if row < 0:
            return
        target = row + direction
        if target < 0 or target >= self.route_table.rowCount():
            return
        values = []
        target_values = []
        for col in range(self.route_table.columnCount()):
            if col == 0:
                values.append(self._route_vessel_at_row(row))
                target_values.append(self._route_vessel_at_row(target))
            elif col == 1:
                values.append(self._route_stop_at_row(row))
                target_values.append(self._route_stop_at_row(target))
            elif col == 2:
                values.append(self._route_destinations_at_row(row))
                target_values.append(self._route_destinations_at_row(target))
            else:
                item = self.route_table.item(row, col)
                target_item = self.route_table.item(target, col)
                values.append(item.text() if item else "")
                target_values.append(target_item.text() if target_item else "")
        self._updating_route_calc = True
        try:
            self._set_route_vessel_cell(row, target_values[0])
            self._set_route_vessel_cell(target, values[0])
            self._set_route_stop_cell(row, target_values[1])
            self._set_route_stop_cell(target, values[1])
            self._set_route_destinations_cell(row, target_values[2])
            self._set_route_destinations_cell(target, values[2])
        finally:
            self._updating_route_calc = False
        self._user_overridden_limits.discard(row)
        self._user_overridden_limits.discard(target)
        self._recalculate_route_rows_from(min(row, target))
        self.route_table.setCurrentCell(target, 0)

    def clear_output(self) -> None:
        self._latest_result = None
        self._current_assignments = None
        self.result_table.setRowCount(0)
        self._reset_vessel_filter([])
        self.summary_text.clear()
        self.issues_text.clear()

    def load_pdf_records(self) -> None:
        pdf_dir = Path(self.pdf_dir_edit.text().strip())
        if not pdf_dir.exists() or not pdf_dir.is_dir():
            QMessageBox.warning(self, "Manifestos", "Informe uma pasta valida com os PDFs.")
            return

        deliveries: List[DeliveryRecord] = []
        transfers: List[TransferRecord] = []
        errors: List[str] = []
        for pdf_path in sorted(pdf_dir.glob("*.pdf")):
            try:
                if "TRANSBORDO" in pdf_path.name.upper():
                    transfers.extend(read_petrobras_transfer_pdf(pdf_path))
                else:
                    deliveries.extend(read_petrobras_delivery_pdf(pdf_path))
            except Exception as exc:
                errors.append(f"{pdf_path.name}: {exc}")

        if not deliveries and not transfers:
            QMessageBox.warning(self, "Manifestos", "Nenhum registro foi lido dos PDFs informados.")
            return

        self._loaded_deliveries = deliveries
        self._loaded_transfers = transfers
        self._read_errors = errors
        self._populate_transfers_table(transfers)
        self._build_pax_snapshot()
        self._refresh_route_stop_and_destination_options()
        self._recalculate_route_rows_from(0)
        self.summary_text.setPlainText(
            f"Entregas lidas: {len(deliveries)}\n"
            f"Transbordos lidos: {len(transfers)}\n"
            "Confira a tabela de transbordos antes de gerar a lista.\n"
            + ("\nErros de leitura:\n- " + "\n- ".join(errors) if errors else "")
        )

    def _populate_transfers_table(self, transfers: List[TransferRecord]) -> None:
        self._updating_manifest_tables = True
        self.transfers_table.setRowCount(0)
        for transfer in transfers:
            row = self.transfers_table.rowCount()
            self.transfers_table.insertRow(row)
            values = [
                "SIM",
                transfer.passenger_name,
                transfer.from_platform,
                transfer.to_platform,
                transfer.timestamp,
                Path(transfer.source).name if transfer.source else "",
            ]
            for col, value in enumerate(values):
                self.transfers_table.setItem(row, col, QTableWidgetItem(value))
        self._updating_manifest_tables = False

    def _confirmed_transfers(self) -> List[TransferRecord]:
        confirmed: List[TransferRecord] = []
        for row, transfer in enumerate(self._loaded_transfers):
            item = self.transfers_table.item(row, 0)
            marker = item.text().strip().upper() if item else "SIM"
            if marker not in {"NAO", "NÃO", "N", "NO", "0", "FALSE"}:
                confirmed.append(transfer)
        return confirmed

    def _read_itineraries_from_table(self) -> List[VesselItinerary]:
        stops_by_vessel: dict[str, List[str]] = {}
        filters_by_vessel: dict[str, dict[str, List[str]]] = {}
        limits_by_vessel: dict[str, dict[str, int]] = {}
        last_vessel = ""
        for row in range(self.route_table.rowCount()):
            vessel = self._route_vessel_at_row(row)
            stop = self._route_stop_at_row(row)
            if not vessel:
                vessel = last_vessel
            if not vessel or not stop:
                continue

            last_vessel = vessel
            stops_by_vessel.setdefault(vessel, []).append(stop)
            destination_text = self._route_destinations_at_row(row).upper()
            if destination_text and destination_text not in {"TODOS", "TODO", "ALL", "*"}:
                destinations = [
                    token.strip()
                    for token in re.split(r"[,;/\s]+", destination_text)
                    if token.strip()
                ]
                if destinations:
                    filters_by_vessel.setdefault(vessel, {})[stop] = destinations
            limit = self._route_limit_at_row(row)
            if limit is not None and limit > 0:
                limits_by_vessel.setdefault(vessel, {})[stop] = limit

        return [
            VesselItinerary(
                vessel=vessel,
                stops=stops,
                pickup_filters=filters_by_vessel.get(vessel, {}),
                pickup_limits=limits_by_vessel.get(vessel, {}),
            )
            for vessel, stops in stops_by_vessel.items()
            if stops
        ]

    def generate_manifest_list(self) -> None:
        if not self._loaded_deliveries and not self._loaded_transfers:
            self.load_pdf_records()
            if not self._loaded_deliveries and not self._loaded_transfers:
                return

        itineraries = self._read_itineraries_from_table()
        if not itineraries:
            QMessageBox.warning(self, "Manifestos", "Monte o roteiro de recolhimento na tabela.")
            return

        transfers = self._confirmed_transfers()
        result = build_passenger_pickup_list(
            self._loaded_deliveries,
            transfers,
            itineraries,
            return_origin_platforms=self._configured_return_origins(),
            vessel_capacities=self._manifest_vessel_capacities(),
        )
        self._latest_result = result
        self._current_assignments = list(result.assignments)
        self._populate_result_table(self._current_assignments)
        self._reset_vessel_filter(result.assignments)
        self.summary_text.setPlainText(
            f"Entregas lidas: {len(self._loaded_deliveries)}\n"
            f"Transbordos lidos: {len(self._loaded_transfers)}\n"
            f"Transbordos aplicados: {len(transfers)}\n"
            f"Passageiros alocados: {len(result.assignments)}\n"
            f"Pendencias: {len(result.issues)}\n"
            + self._loads_summary(result)
            + ("\nErros de leitura:\n- " + "\n- ".join(self._read_errors) if self._read_errors else "")
        )
        self.issues_text.setPlainText(self._issues_text(result.issues))

    def _manifest_vessel_capacities(self) -> dict[str, int]:
        op_config = self.parent_window.current_op_config
        capacities: dict[str, int] = {}
        for vessel in getattr(op_config, "frota", []) or []:
            name = str(getattr(vessel, "nome", "")).strip()
            if not name:
                continue
            capacities[name] = int(getattr(vessel, "capacidade", 24) or 24)
        return capacities

    @staticmethod
    def _loads_summary(result: PickupListResult) -> str:
        if not result.route_loads:
            return ""
        lines = ["\nCarga por embarcacao:"]
        for vessel in sorted(result.route_loads):
            capacity = result.route_capacities.get(vessel)
            if capacity:
                lines.append(f"- {vessel}: {result.route_loads[vessel]}/{capacity} pax")
            else:
                lines.append(f"- {vessel}: {result.route_loads[vessel]} pax")
        return "\n".join(lines) + "\n"

    def _on_result_pax_cell_clicked(self, row: int, col: int) -> None:
        """Open pax selection dialog when user clicks the Passageiro column (col 3)."""
        if col != 3:
            return
        if self._latest_result is None or self._current_assignments is None:
            return
        vessel_item = self.result_table.item(row, 0)
        platform_item = self.result_table.item(row, 1)
        dest_item = self.result_table.item(row, 2)
        if not vessel_item or not platform_item or not dest_item:
            return
        self._open_pax_selection(
            vessel_item.text().strip(),
            platform_item.text().strip(),
            dest_item.text().strip(),
        )

    def _on_add_pax_clicked(self) -> None:
        """Button handler: choose vessel/platform/destination then open selection dialog."""
        if self._latest_result is None or self._current_assignments is None:
            QMessageBox.information(self, "Manifestos", "Gere a lista primeiro.")
            return

        vessels = sorted(self._latest_result.route_capacities.keys())
        if not vessels:
            QMessageBox.information(self, "Manifestos", "Nenhuma embarcacao no roteiro.")
            return

        platforms_dests: dict[str, set] = {}
        for entry in self._latest_result.ledger.values():
            plat = entry.current_platform
            dest = entry.return_destination
            if plat == dest or plat.upper() == "TMIB":
                continue
            platforms_dests.setdefault(plat, set()).add(dest)
        platforms_dests_sorted = {k: sorted(v) for k, v in sorted(platforms_dests.items())}

        if not platforms_dests_sorted:
            QMessageBox.information(self, "Manifestos", "Nao ha passageiros aguardando recolhimento.")
            return

        dlg = _AddPaxChooser(vessels, platforms_dests_sorted, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        self._open_pax_selection(dlg.vessel, dlg.platform, dlg.destination)

    def _open_pax_selection(self, vessel: str, platform: str, destination: str) -> None:
        """Open PaxSelectionDialog for (vessel, platform, destination) and commit changes."""
        all_pax = [
            entry
            for entry in self._latest_result.ledger.values()
            if entry.current_platform.upper() == platform.upper()
            and entry.return_destination.upper() == destination.upper()
        ]

        assigned_to_vessel = {
            a.passenger_name
            for a in self._current_assignments
            if a.vessel == vessel
            and a.pickup_platform.upper() == platform.upper()
            and a.return_destination.upper() == destination.upper()
        }
        assigned_to_others: dict[str, str] = {
            a.passenger_name: a.vessel
            for a in self._current_assignments
            if a.vessel != vessel
            and a.pickup_platform.upper() == platform.upper()
            and a.return_destination.upper() == destination.upper()
        }

        available_pax = [p for p in all_pax if p.passenger_name not in assigned_to_others]
        if not available_pax and not assigned_to_others:
            QMessageBox.information(
                self, "Manifestos", f"Nao ha passageiros em {platform}→{destination}."
            )
            return

        vessel_capacity = (
            self._latest_result.route_capacities.get(vessel, 24)
            if self._latest_result.route_capacities else 24
        )
        load_other_stops = sum(
            1 for a in self._current_assignments
            if a.vessel == vessel
            and not (
                a.pickup_platform.upper() == platform.upper()
                and a.return_destination.upper() == destination.upper()
            )
        )
        max_at_stop = max(0, vessel_capacity - load_other_stops)

        sel_dlg = PaxSelectionDialog(
            vessel, platform, destination, available_pax,
            assigned_to_vessel, assigned_to_others, max_at_stop, self
        )
        if sel_dlg.exec_() != QDialog.Accepted:
            return

        new_selected = sel_dlg.selected_names()

        kept = [
            a for a in self._current_assignments
            if not (
                a.vessel == vessel
                and a.pickup_platform.upper() == platform.upper()
                and a.return_destination.upper() == destination.upper()
            )
        ]
        original_lookup: dict[str, PassengerAssignment] = {
            a.passenger_name: a
            for a in self._latest_result.assignments
            if a.pickup_platform.upper() == platform.upper()
            and a.return_destination.upper() == destination.upper()
        }
        new_rows: List[PassengerAssignment] = []
        for entry in sorted(all_pax, key=lambda e: e.passenger_name.upper()):
            if entry.passenger_name not in new_selected:
                continue
            orig = original_lookup.get(entry.passenger_name)
            new_rows.append(
                PassengerAssignment(
                    vessel=vessel,
                    pickup_platform=platform,
                    return_destination=destination,
                    passenger_name=entry.passenger_name,
                    passenger_id=orig.passenger_id if orig else entry.passenger_id,
                    company=orig.company if orig else entry.company,
                    sources=list(orig.sources if orig else entry.sources),
                    movement_history=list(orig.movement_history if orig else entry.movement_history),
                    notes=orig.notes if orig else "",
                )
            )

        self._current_assignments = kept + new_rows
        self._current_assignments.sort(
            key=lambda a: (a.vessel, a.pickup_platform, a.return_destination, a.passenger_name.upper())
        )
        self._populate_result_table(self._current_assignments)
        self._reset_vessel_filter(self._current_assignments)

    def _populate_result_table(self, assignments: List[PassengerAssignment]) -> None:
        self.result_table.setRowCount(0)
        for item in assignments:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            values = [
                item.vessel,
                item.pickup_platform,
                item.return_destination,
                item.passenger_name,
                item.passenger_id,
                " | ".join(item.sources),
            ]
            for col, value in enumerate(values):
                self.result_table.setItem(row, col, QTableWidgetItem(value))
        self._apply_result_filter()

    def _reset_vessel_filter(self, assignments: List[PassengerAssignment]) -> None:
        current = self.vessel_filter_combo.currentText()
        self.vessel_filter_combo.blockSignals(True)
        self.vessel_filter_combo.clear()
        self.vessel_filter_combo.addItem("TODAS")
        for vessel in sorted({item.vessel for item in assignments}):
            self.vessel_filter_combo.addItem(vessel)
        index = self.vessel_filter_combo.findText(current)
        self.vessel_filter_combo.setCurrentIndex(index if index >= 0 else 0)
        self.vessel_filter_combo.blockSignals(False)
        self._apply_result_filter()

    def _apply_result_filter(self) -> None:
        selected = self.vessel_filter_combo.currentText().strip()
        for row in range(self.result_table.rowCount()):
            vessel_item = self.result_table.item(row, 0)
            vessel = vessel_item.text().strip() if vessel_item else ""
            self.result_table.setRowHidden(row, bool(selected and selected != "TODAS" and vessel != selected))

    @staticmethod
    def _issues_text(issues: List[AssignmentIssue]) -> str:
        if not issues:
            return "Sem pendencias."
        lines: List[str] = []
        for issue in issues:
            who = issue.passenger_name or issue.passenger_id or issue.platform
            lines.append(f"[{issue.severity.upper()}] {issue.code}: {who} - {issue.message}")
        return "\n".join(lines)

    def _effective_result(self) -> Optional[PickupListResult]:
        """Return a PickupListResult reflecting any manual assignment changes."""
        if self._latest_result is None:
            return None
        if not self._current_assignments:
            return self._latest_result
        from collections import defaultdict
        loads: dict[str, int] = defaultdict(int)
        for a in self._current_assignments:
            loads[a.vessel] += 1
        return PickupListResult(
            assignments=list(self._current_assignments),
            issues=self._latest_result.issues,
            ledger=self._latest_result.ledger,
            route_loads=dict(loads),
            route_capacities=self._latest_result.route_capacities,
        )

    def export_csv(self) -> None:
        effective = self._effective_result()
        if effective is None:
            QMessageBox.warning(self, "Manifestos", "Gere a lista antes de exportar.")
            return
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar lista por embarcacao em CSV",
            "lista_passageiros_recolhimento.csv",
            "CSV (*.csv)",
        )
        if not file_name:
            return
        export_assignments_csv(effective, Path(file_name))
        QMessageBox.information(self, "Manifestos", f"Arquivo salvo em:\n{file_name}")

    def export_printable_manifest(self) -> None:
        effective = self._effective_result()
        if effective is None:
            QMessageBox.warning(self, "Manifestos", "Gere a lista antes de exportar.")
            return
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar impressao dos manifestos",
            "manifestos_recolhimento.txt",
            "TXT (*.txt)",
        )
        if not file_name:
            return
        Path(file_name).write_text(self._printable_manifest_text(effective), encoding="utf-8")
        QMessageBox.information(self, "Manifestos", f"Arquivo salvo em:\n{file_name}")

    @staticmethod
    def _printable_manifest_text(result: PickupListResult) -> str:
        grouped: dict[str, dict[str, dict[str, List[PassengerAssignment]]]] = {}
        for item in result.assignments:
            grouped.setdefault(item.vessel, {}).setdefault(item.pickup_platform, {}).setdefault(
                item.return_destination, []
            ).append(item)

        lines: List[str] = []
        for vessel in sorted(grouped):
            lines.extend(
                [
                    "=" * 78,
                    "PETROBRAS".center(78),
                    "MANIFESTO - RECOLHIMENTO DE PASSAGEIROS".center(78),
                    f"EMBARCACAO: {vessel}",
                    "-" * 78,
                    f"{'ORIGEM':<10} {'DESTINO':<10} {'PASSAGEIRO':<38} {'DOCUMENTO':<16}",
                    "-" * 78,
                ]
            )
            for platform in grouped[vessel]:
                for destination in grouped[vessel][platform]:
                    passengers = sorted(
                        grouped[vessel][platform][destination],
                        key=lambda item: item.passenger_name.upper(),
                    )
                    for passenger in passengers:
                        lines.append(
                            f"{platform:<10} {destination:<10} {passenger.passenger_name[:38]:<38} {passenger.passenger_id[:16]:<16}"
                        )
            load = result.route_loads.get(vessel, 0)
            capacity = result.route_capacities.get(vessel)
            lines.append("-" * 78)
            lines.append(f"TOTAL PAX: {load}" + (f" / CAPACIDADE: {capacity}" if capacity else ""))
            lines.append("")
        if result.issues:
            lines.extend(["PENDENCIAS E ALERTAS", "-" * 78])
            for issue in result.issues:
                who = issue.passenger_name or issue.passenger_id or issue.platform
                lines.append(f"[{issue.severity.upper()}] {issue.code}: {who} - {issue.message}")
        return "\n".join(lines)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.service = AppService()
        self.current_root = ""
        self.current_operation: Optional[OperationMetadata] = None
        self.current_op_config: Optional[OperationalConfig] = None
        self._build()
        self.reload_config()
        self._apply_styles()

    def _build(self) -> None:
        self.setWindowTitle("Roteirizador Desktop V2 - Manifestos")
        self.resize(1440, 960)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)

        left = QVBoxLayout()
        self.new_op_btn = QPushButton("Nova operacao")
        self.new_op_btn.clicked.connect(self.create_operation)
        self.rename_op_btn = QPushButton("Renomear operacao")
        self.rename_op_btn.clicked.connect(self.rename_operation)
        self.delete_op_btn = QPushButton("Excluir operacao")
        self.delete_op_btn.clicked.connect(self.delete_operation)
        self.operations_list = QListWidget()
        self.operations_list.itemSelectionChanged.connect(self.select_operation)
        left.addWidget(self.new_op_btn)
        left.addWidget(self.rename_op_btn)
        left.addWidget(self.delete_op_btn)
        left.addWidget(QLabel("Operacoes"))
        left.addWidget(self.operations_list)

        right = QTabWidget()
        self.config_tab = ConfigTab(self.service, self)
        self.programacao_tab = VersionEditor(self.service, self, VERSION_CL)
        self.pickup_tab = PickupTab(self.service, self)
        self.manifest_tab = PassengerManifestTab(self.service, self)
        self.distribuicao_tab = ManifestosDistribuicaoTab(self)
        right.addTab(self.config_tab, "Configuracoes")
        right.addTab(self.programacao_tab, "Programacao")
        recolhimento_idx = right.addTab(self.pickup_tab, "Recolhimento")
        right.setTabEnabled(recolhimento_idx, False)
        manifestos_rec_idx = right.addTab(self.manifest_tab, "Manifestos Recolhimento")
        right.setTabEnabled(manifestos_rec_idx, False)
        right.addTab(self.distribuicao_tab, "Manifestos Distribuicao")

        splitter = QSplitter()
        left_container = QWidget()
        left_container.setLayout(left)
        splitter.addWidget(left_container)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

    def _apply_styles(self) -> None:
        # Premium / Modern Style Sheet
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #f4f6f9;
                font-family: "Segoe UI", "Helvetica Neue", "Arial", sans-serif;
                font-size: 9pt;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #dcdcdc;
                border-radius: 6px;
                margin-top: 12px;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 5px;
                color: #2c3e50;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:disabled {
                background-color: #bdc3c7;
            }
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f9f9f9;
                gridline-color: #ecf0f1;
                selection-background-color: #3498db;
                border: 1px solid #dcdcdc;
            }
            QTableWidget#tabelaDistribuicao::item:selected,
            QTableWidget#tabelaDistribuicao::item:selected:!active {
                background-color: #1f618d;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #ecf0f1;
                padding: 4px;
                border: 1px solid #dcdcdc;
                font-weight: bold;
                color: #2c3e50;
            }
            QLineEdit, QTextEdit {
                border: 1px solid #bdc3c7;
                border-radius: 4px;
                padding: 4px;
                background-color: #ffffff;
            }
            QListWidget {
                border: 1px solid #dcdcdc;
                background-color: #ffffff;
            }
            QListWidget::item {
                padding: 8px;
            }
            QListWidget::item:selected {
                background-color: #3498db;
                color: white;
            }
            QTabWidget::pane {
                border: 1px solid #dcdcdc;
                background-color: #ffffff;
            }
            QTabBar::tab {
                background: #ecf0f1;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                border-bottom: 2px solid #3498db;
            }
        """)

    def reload_config(self) -> None:
        app_config = self.service.load_app_config()
        self.current_root = app_config.storage_root
        op_config = None
        selected_operation_id = self.current_operation.operacao_id if self.current_operation else None
        if self.current_root:
            try:
                self.service.bootstrap_network_config(self.current_root)
                op_config = self.service.load_operational_config(self.current_root)
            except Exception as exc:
                QMessageBox.warning(self, "Configuracao", str(exc))
        self.current_op_config = op_config
        self.config_tab.load(app_config, op_config)
        self.reload_operations(select_operation_id=selected_operation_id)
        if not selected_operation_id:
            self.refresh_operation()

    def reload_operations(self, select_operation_id: Optional[str] = None) -> None:
        self.operations_list.clear()
        if not self.current_root:
            return
        for operation in self.service.list_operations(self.current_root):
            item = QListWidgetItem(f"{operation.data_operacao} | {operation.label()}")
            item.setData(Qt.UserRole, operation)
            self.operations_list.addItem(item)
            if select_operation_id and operation.operacao_id == select_operation_id:
                self.operations_list.setCurrentItem(item)

    def create_operation(self) -> None:
        if not self.current_root:
            QMessageBox.warning(self, "Operacao", "Pasta compartilhada indisponivel.")
            return
        operation_date, ok = QInputDialog.getText(
            self, "Nova operacao", "Data da operacao (YYYY-MM-DD):", text=today_iso()
        )
        if not ok or not operation_date:
            return
        self.current_operation = self.service.create_operation(self.current_root, operation_date)
        self.reload_operations(select_operation_id=self.current_operation.operacao_id)
        self.refresh_operation()

    def rename_operation(self) -> None:
        if not self.current_root or not self.current_operation:
            QMessageBox.warning(self, "Operacao", "Selecione uma operacao.")
            return
        new_display_name, ok = QInputDialog.getText(
            self,
            "Renomear operacao",
            "Novo nome exibido da operacao:",
            text=self.current_operation.display_name or self.current_operation.label(),
        )
        if not ok or not new_display_name.strip():
            return
        try:
            renamed = self.service.rename_operation(
                self.current_root,
                self.current_operation,
                new_display_name.strip(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Operacao", f"Nao foi possivel renomear:\n{exc}")
            return
        self.current_operation = renamed
        self.reload_operations(select_operation_id=self.current_operation.operacao_id)
        self.refresh_operation()

    def delete_operation(self) -> None:
        if not self.current_root or not self.current_operation:
            QMessageBox.warning(self, "Operacao", "Selecione uma operacao.")
            return
        response = QMessageBox.question(
            self,
            "Excluir operacao",
            f"Deseja excluir a operacao {self.current_operation.label()}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if response != QMessageBox.Yes:
            return
        try:
            self.service.delete_operation(self.current_root, self.current_operation)
        except Exception as exc:
            QMessageBox.warning(self, "Operacao", f"Nao foi possivel excluir:\n{exc}")
            return
        self.current_operation = None
        self.reload_operations()
        self.refresh_operation()

    def select_operation(self) -> None:
        items = self.operations_list.selectedItems()
        if not items:
            return
        self.current_operation = items[0].data(Qt.UserRole)
        self.refresh_operation()

    def refresh_operation(self) -> None:
        user_name = ""
        self.programacao_tab.load_bundle(
            self.service.load_version(self.current_root, self.current_operation, VERSION_CL)
            if self.current_operation and self.current_root
            else None,
            user_name,
            self.current_op_config,
        )
        if self.current_operation and self.current_root:
            self.pickup_tab.version_combo.setCurrentIndex(0)
            self.pickup_tab.set_operation()
        else:
            self.pickup_tab.clear("Selecione uma operacao para planejar o recolhimento.")


def run() -> int:
    if IMPORT_ERROR is not None:  # pragma: no cover
        raise SystemExit(
            "PySide6 nao esta instalado no ambiente atual. Instale a dependencia para executar a interface."
        ) from IMPORT_ERROR
    app = QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()
