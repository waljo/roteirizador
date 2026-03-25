from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import time
from typing import List, Optional

from .domain import (
    AppConfig,
    AvailableBoat,
    DemandItem,
    FleetVessel,
    OperationalConfig,
    OperationMetadata,
    OperationVersion,
    SolverRunResult,
    VersionBundle,
    VERSION_CL,
    VERSION_PROGRAMACAO,
)
from .services import AppService, default_operation_version, today_iso

LAYOUT_SPEC_VERSION = "1.1.0"
HELP_SECTION_MAX_HEIGHT = 220
BUTTON_GRID_SPACING = 6
USE_COLLAPSIBLE_SPLITTERS = False

ROUTE_HELP_TEXT = """COMO DIGITAR A ROTA:

+x = embarque no TMIB ou em M9

-x = desembarque de pax do TMIB

(-x) = desembarque de pax de M9

{destino:+x} = embarque em outra plataforma
Ex.: O pax esta embarcando em M6 com destino a B1, coloque {B1:+1}

{origem:-x} = desembarque de pax de outra origem
Ex.: O pax de M6 desembarcando em B1, coloque {M6:-1}

EXEMPLO:
Lancha pega 22 no TMIB -> M10 deixa 5 -> M9 deixa 7 e pega 4 p/ B2 -> M6 deixa 4 e pega 1 p/ B1 -> B2 deixa 8 (4 TMIB + 4 M9) -> B1 deixa 3 (2 TMIB + 1 M6).
Rota: TMIB +22/M10 -5/M9 -7 +4/M6 -4 {B1:+1}/B2 -4 (-4)/B1 -2 {M6:-1}
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
- Rotas Fixas com saida ate 15:00 abatem da demanda automaticamente.
- Rotas Fixas com saida apos 15:00 nao abatem da demanda (apenas aparecem na distribuicao).

Exemplos de Rotas Fixas:
- Operacao iniciando as 05:10 em M6 (troca de turma da sonda e embarque em M10) abate da demanda.
- Operacao iniciando as 17:00 em M9 (troca de turma da sonda) nao abate da demanda.

Em caso de duvida, verifique operacoes de dias anteriores.
"""

try:
    from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
    from PySide6.QtWidgets import (
        QHeaderView,
        QApplication,
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

    QObject = object
    QThread = object
    QTimer = object
    QHeaderView = object
    Qt = _QtFallback()
    QApplication = None
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
    QSplitter = object
    QTableWidget = object
    QTableWidgetItem = object
    QTabWidget = object
    QTextEdit = object
    QVBoxLayout = object
    QWidget = object
    QInputDialog = object
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


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
                "Desemb. M9 (-)",
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
        for raw_part in route_text.split("/"):
            part = raw_part.strip()
            if not part:
                continue
            tokens = part.split()
            if not tokens:
                continue
            row = {
                "platform": tokens[0],
                "pickup": "0",
                "drop_tmib": "0",
                "drop_m9": "0",
                "plus_dest": "",
                "plus_qty": "0",
                "minus_origin": "",
                "minus_qty": "0",
            }
            for token in tokens[1:]:
                if token.startswith("+") and token[1:].isdigit():
                    row["pickup"] = token[1:]
                elif token.startswith("(-") and token.endswith(")") and token[2:-1].isdigit():
                    row["drop_m9"] = token[2:-1]
                elif token.startswith("-") and token[1:].isdigit():
                    row["drop_tmib"] = token[1:]
                else:
                    trans_plus = re.fullmatch(r"\{([^:{}]+):\+(\d+)\}", token)
                    if trans_plus:
                        row["plus_dest"] = trans_plus.group(1)
                        row["plus_qty"] = trans_plus.group(2)
                        continue
                    trans_minus = re.fullmatch(r"\{([^:{}]+):-(\d+)\}", token)
                    if trans_minus:
                        row["minus_origin"] = trans_minus.group(1)
                        row["minus_qty"] = trans_minus.group(2)
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
            if pickup > 0:
                tokens.append(f"+{pickup}")
            if drop_tmib > 0:
                tokens.append(f"-{drop_tmib}")
            if drop_m9 > 0:
                tokens.append(f"(-{drop_m9})")
            if plus_dest and plus_qty > 0:
                tokens.append(f"{{{plus_dest}:+{plus_qty}}}")
            if minus_origin and minus_qty > 0:
                tokens.append(f"{{{minus_origin}:-{minus_qty}}}")
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
        self.fleet_table = AutoAppendTableWidget(0, 5)
        self.gangway_table = AutoAppendTableWidget(0, 1)
        self.conves_table = AutoAppendTableWidget(0, 1)
        self.storage_label = QLabel()
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.storage_label.setWordWrap(True)
        form.addRow("Pasta compartilhada", self.storage_label)
        layout.addLayout(form)

        fleet_box = QGroupBox("Frota")
        fleet_layout = QVBoxLayout(fleet_box)
        self.fleet_table.set_append_row_callback(self.add_fleet_row)
        self.fleet_table.set_remove_row_callback(self.remove_selected_rows)
        self.fleet_table.set_block_delete_backspace(True)
        self.fleet_table.setHorizontalHeaderLabels(
            ["Nome", "Tipo", "Capacidade", "Velocidade", "Ativa"]
        )
        fleet_layout.addWidget(self.fleet_table)
        fleet_btns = QHBoxLayout()
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
        self.gangway_table.set_append_row_callback(self.add_gangway_row)
        self.gangway_table.set_block_delete_backspace(True)
        self.gangway_table.setHorizontalHeaderLabels(["Unidade"])
        gangway_layout.addWidget(self.gangway_table)
        add_gangway = QPushButton("Adicionar unidade")
        add_gangway.clicked.connect(self.add_gangway_row)
        gangway_layout.addWidget(add_gangway)
        layout.addWidget(gangway_box)

        conves_box = QGroupBox("Embarcacoes de conves")
        conves_layout = QVBoxLayout(conves_box)
        self.conves_table.set_append_row_callback(self.add_conves_row)
        self.conves_table.set_remove_row_callback(self.remove_selected_rows)
        self.conves_table.set_block_delete_backspace(True)
        self.conves_table.setHorizontalHeaderLabels(["Embarcacao"])
        conves_layout.addWidget(self.conves_table)
        conves_btns = QHBoxLayout()
        add_conves = QPushButton("Adicionar embarcacao de conves")
        add_conves.clicked.connect(self.add_conves_row)
        remove_conves = QPushButton("Excluir embarcacao selecionada")
        remove_conves.clicked.connect(lambda: self.remove_selected_rows(self.conves_table))
        conves_btns.addWidget(add_conves)
        conves_btns.addWidget(remove_conves)
        conves_layout.addLayout(conves_btns)
        layout.addWidget(conves_box)

        save_btn = QPushButton("Salvar configuracao")
        save_btn.clicked.connect(self.save_config)
        layout.addWidget(save_btn)

    def load(self, app_config: AppConfig, op_config: Optional[OperationalConfig]) -> None:
        self.storage_label.setText(app_config.storage_root)
        self.fleet_table.setRowCount(0)
        self.gangway_table.setRowCount(0)
        self.conves_table.setRowCount(0)
        if not op_config:
            return
        for vessel in op_config.frota:
            self.add_fleet_row(vessel)
        for item in op_config.gangway:
            self.add_gangway_row(item)
        for item in op_config.embarcacoes_conves:
            self.add_conves_row(item)

    def add_fleet_row(self, vessel: Optional[FleetVessel] = None) -> None:
        row = self.fleet_table.rowCount()
        self.fleet_table.insertRow(row)
        values = [
            vessel.nome if vessel else "",
            vessel.tipo if vessel else "surfer",
            str(vessel.capacidade if vessel else 24),
            str(vessel.velocidade if vessel else 14.0),
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

    def save_config(self) -> None:
        root = self.parent_window.current_root
        vessels: List[FleetVessel] = []
        for row in range(self.fleet_table.rowCount()):
            nome = self._text(self.fleet_table, row, 0)
            if not nome:
                continue
            vessels.append(
                FleetVessel(
                    nome=nome,
                    tipo=self._text(self.fleet_table, row, 1) or "surfer",
                    capacidade=int(self._text(self.fleet_table, row, 2) or 24),
                    velocidade=float(self._text(self.fleet_table, row, 3) or 14.0),
                    ativa=(self._text(self.fleet_table, row, 4).upper() == "SIM"),
                )
            )
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
        self.service.save_operational_config(
            root,
            OperationalConfig(
                frota=vessels,
                unidades=self.parent_window.current_op_config.unidades if self.parent_window.current_op_config else [],
                gangway=gangway,
                embarcacoes_conves=embarcacoes_conves,
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
        self.demand_table = AutoAppendTableWidget(0, 4)
        self.output_text = QTextEdit()
        self.manual_route_text = QTextEdit()
        self.export_program_button = QPushButton("Exportar planilha")
        self.export_cl_txt_button = QPushButton("Exportar TXT de distribuicao")
        self.compare_routes_button = QPushButton("Comparar roteiros")
        self.imported_csv_path: Optional[Path] = None
        self._solver_thread: Optional[QThread] = None
        self._solver_worker: Optional[SolverRunWorker] = None
        self._solver_dialog: Optional[SolverProgressDialog] = None
        self._build()

    @staticmethod
    def _set_compact_grid_button(button: QPushButton) -> None:
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        button.setMinimumSize(0, 0)
        button.setStyleSheet("padding: 0px 8px; margin: 0px; min-width: 0px; min-height: 0px;")

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
        route_hint_section = CollapsibleSection("Como digitar rota", expanded=False)
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
        self.demand_table.setHorizontalHeaderLabels(["Plataforma", "M9", "TMIB", "Prioridade"])
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
        content_layout.setStretch(1, 1)  # Splitter ocupa o espaco restante
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
        self.demand_table.setRowCount(0)
        self.output_text.clear()
        self.manual_route_text.clear()
        self.export_program_button.setEnabled(False)
        self.export_cl_txt_button.setEnabled(False)
        self.imported_csv_path = None
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
                tokens.append(f"(-{int(stop['drop_m9'])})")
            for destination, qty in stop.get("plus_transfers", {}).items():
                if int(qty) > 0:
                    tokens.append(f"{{{destination}:+{int(qty)}}}")
            for origin, qty in stop.get("minus_transfers", {}).items():
                if int(qty) > 0:
                    tokens.append(f"{{{origin}:-{int(qty)}}}")
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
            str(demand.m9 if demand else 0),
            str(demand.tmib if demand else 0),
            str(demand.prioridade if demand else 0),
        ]
        for col, value in enumerate(values):
            self.demand_table.setItem(row, col, QTableWidgetItem(value))

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
            demands.append(
                DemandItem(
                    plataforma=plataforma,
                    m9=int(self._text(self.demand_table, row, 1) or 0),
                    tmib=int(self._text(self.demand_table, row, 2) or 0),
                    prioridade=int(self._text(self.demand_table, row, 3) or 0),
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
        file_name, _ = QFileDialog.getOpenFileName(self, "Selecionar Extrato PDF", filter="PDF (*.pdf)")
        if not file_name:
            return
        try:
            demands = self.service.import_extrato_pdf(Path(file_name))
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
            rows.append(
                {
                    "PLATAFORMA": plataforma,
                    "M9": self._text(self.demand_table, row, 1) or "0",
                    "TMIB": self._text(self.demand_table, row, 2) or "0",
                    "PRIORIDADE": self._text(self.demand_table, row, 3) or "0",
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
            default_name = f"{self.parent_window.current_operation.operacao_id}_{self.version_name}_demanda.csv"
        else:
            default_name = f"{self.version_name}_demanda.csv"

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
            writer = csv.DictWriter(
                handle,
                fieldnames=["PLATAFORMA", "M9", "TMIB", "PRIORIDADE"],
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

    def _on_solver_progress(self, message: str) -> None:
        if self._solver_dialog is not None:
            self._solver_dialog.append_message(message)

    def _on_solver_finished(self, updated_operation: OperationMetadata, result: SolverRunResult) -> None:
        self.parent_window.current_operation = updated_operation
        self.output_text.setPlainText(result.distribution_text)
        self.export_program_button.setEnabled(bool(result.distribution_text.strip()))
        self.export_cl_txt_button.setEnabled(bool(result.distribution_text.strip()))
        self.parent_window.reload_operations(select_operation_id=updated_operation.operacao_id)
        self.parent_window.refresh_operation()
        if self._solver_dialog is not None:
            self._solver_dialog.append_message("Distribuicao concluida com sucesso.")
            self._solver_dialog.finish(success=True)
            self._solver_dialog.accept()
        if self.version_name in (VERSION_PROGRAMACAO, VERSION_CL):
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
        if not operation:
            return f"{self.version_name}_programacao.xlsx"
        date_token = operation.data_operacao.replace("-", "_")
        return f"{date_token}_{operation.operacao_id}_{self.version_name}_programacao.xlsx"

    def _default_cl_distribution_txt_name(self) -> str:
        operation = self.parent_window.current_operation
        if not operation:
            return f"{self.version_name}_distribuicao.txt"
        date_token = operation.data_operacao.replace("-", "_")
        return f"{date_token}_{operation.operacao_id}_{self.version_name}_distribuicao.txt"

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
        self.setWindowTitle("Roteirizador Desktop")
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
        self.programacao_tab = VersionEditor(self.service, self, VERSION_PROGRAMACAO)
        self.cl_tab = VersionEditor(self.service, self, VERSION_CL)
        self.comparison_tab = ComparisonTab(self.service, self)
        right.addTab(self.config_tab, "Configuracoes")
        right.addTab(self.programacao_tab, "Programacao")
        right.addTab(self.cl_tab, "CL Oficial")
        right.addTab(self.comparison_tab, "Comparacao")

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
        if self.current_root:
            try:
                self.service.bootstrap_network_config(self.current_root)
                op_config = self.service.load_operational_config(self.current_root)
            except Exception as exc:
                QMessageBox.warning(self, "Configuracao", str(exc))
        self.current_op_config = op_config
        self.config_tab.load(app_config, op_config)
        self.reload_operations()

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
            self.service.load_version(self.current_root, self.current_operation, VERSION_PROGRAMACAO)
            if self.current_operation and self.current_root
            else None,
            user_name,
            self.current_op_config,
        )
        self.cl_tab.load_bundle(
            self.service.load_version(self.current_root, self.current_operation, VERSION_CL)
            if self.current_operation and self.current_root
            else None,
            user_name,
            self.current_op_config,
        )
        if self.current_operation and self.current_root:
            self.comparison_tab.load(self.service.load_comparison(self.current_root, self.current_operation))
        else:
            self.comparison_tab.load(None)


def run() -> int:
    if IMPORT_ERROR is not None:  # pragma: no cover
        raise SystemExit(
            "PySide6 nao esta instalado no ambiente atual. Instale a dependencia para executar a interface."
        ) from IMPORT_ERROR
    app = QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()
