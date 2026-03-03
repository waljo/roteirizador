from __future__ import annotations

import json
from pathlib import Path
import re
from typing import List, Optional

from .domain import (
    AppConfig,
    AvailableBoat,
    DemandItem,
    FleetVessel,
    OperationalConfig,
    OperationMetadata,
    OperationVersion,
    VersionBundle,
    VERSION_CL,
    VERSION_PROGRAMACAO,
)
from .services import AppService, default_operation_version, today_iso

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

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
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
        UserRole = 0
        Vertical = 0

    Qt = _QtFallback()
    QApplication = None
    QCheckBox = object
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

    def set_append_row_callback(self, callback) -> None:
        self._append_row_callback = callback

    def set_remove_row_callback(self, callback) -> None:
        self._remove_row_callback = callback

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
        if event.key() == Qt.Key_Delete and self._remove_row_callback is not None:
            self._remove_row_callback(self)
            return
        super().keyPressEvent(event)


class ConfigTab(QWidget):
    def __init__(self, service: AppService, parent_window: "MainWindow"):
        super().__init__()
        self.service = service
        self.parent_window = parent_window
        self.fleet_table = AutoAppendTableWidget(0, 5)
        self.gangway_table = AutoAppendTableWidget(0, 1)
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
        self.gangway_table.setHorizontalHeaderLabels(["Unidade"])
        gangway_layout.addWidget(self.gangway_table)
        add_gangway = QPushButton("Adicionar unidade")
        add_gangway.clicked.connect(self.add_gangway_row)
        gangway_layout.addWidget(add_gangway)
        layout.addWidget(gangway_box)

        save_btn = QPushButton("Salvar configuracao")
        save_btn.clicked.connect(self.save_config)
        layout.addWidget(save_btn)

    def load(self, app_config: AppConfig, op_config: Optional[OperationalConfig]) -> None:
        self.storage_label.setText(app_config.storage_root)
        self.fleet_table.setRowCount(0)
        self.gangway_table.setRowCount(0)
        if not op_config:
            return
        for vessel in op_config.frota:
            self.add_fleet_row(vessel)
        for item in op_config.gangway:
            self.add_gangway_row(item)

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
        self.service.save_operational_config(
            root,
            OperationalConfig(
                frota=vessels,
                unidades=self.parent_window.current_op_config.unidades if self.parent_window.current_op_config else [],
                gangway=gangway,
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
        self.troca_check = QCheckBox("Troca de turma")
        self.rendidos_edit = QLineEdit("0")
        self.boats_table = AutoAppendTableWidget(0, 3)
        self.demand_table = AutoAppendTableWidget(0, 4)
        self.output_text = QTextEdit()
        self.export_program_button = QPushButton("Exportar planilha de programacao")
        self.export_cl_txt_button = QPushButton("Exportar TXT de distribuicao")
        self.imported_csv_path: Optional[Path] = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.addWidget(QLabel("Usuario"), 0, 0)
        form.addWidget(self.user_edit, 0, 1)
        form.addWidget(self.troca_check, 1, 0)
        form.addWidget(QLabel("Rendidos M9"), 1, 2)
        form.addWidget(self.rendidos_edit, 1, 3)
        help_button = QPushButton("Como digitar rota")
        help_button.clicked.connect(self.show_route_help)
        form.addWidget(help_button, 2, 0, 1, 2)
        layout.addLayout(form)

        boats_box = QGroupBox("Embarcacoes disponiveis")
        boats_layout = QVBoxLayout(boats_box)
        self.boats_table.set_append_row_callback(self.add_boat_row)
        self.boats_table.set_remove_row_callback(self.remove_selected_rows)
        self.boats_table.setHorizontalHeaderLabels(["Nome", "Hora saida", "Rota fixa"])
        boats_layout.addWidget(self.boats_table)
        boats_btns = QHBoxLayout()
        add_boat = QPushButton("Adicionar embarcacao")
        add_boat.clicked.connect(self.add_boat_row)
        remove_boat = QPushButton("Excluir embarcacao selecionada")
        remove_boat.clicked.connect(lambda: self.remove_selected_rows(self.boats_table))
        boats_btns.addWidget(add_boat)
        boats_btns.addWidget(remove_boat)
        boats_layout.addLayout(boats_btns)
        layout.addWidget(boats_box)

        demand_box = QGroupBox("Demanda")
        demand_layout = QVBoxLayout(demand_box)
        self.demand_table.set_append_row_callback(self.add_demand_row)
        self.demand_table.set_remove_row_callback(self.remove_selected_rows)
        self.demand_table.setHorizontalHeaderLabels(["Plataforma", "M9", "TMIB", "Prioridade"])
        demand_layout.addWidget(self.demand_table)
        demand_btns = QHBoxLayout()
        add_demand = QPushButton("Adicionar demanda")
        add_demand.clicked.connect(self.add_demand_row)
        remove_demand = QPushButton("Excluir demanda selecionada")
        remove_demand.clicked.connect(lambda: self.remove_selected_rows(self.demand_table))
        import_csv = QPushButton("Importar CSV")
        import_csv.clicked.connect(self.import_csv)
        demand_btns.addWidget(add_demand)
        demand_btns.addWidget(remove_demand)
        demand_btns.addWidget(import_csv)
        demand_layout.addLayout(demand_btns)
        layout.addWidget(demand_box)

        action_row = QHBoxLayout()
        save_btn = QPushButton("Salvar")
        save_btn.clicked.connect(self.save_only)
        run_btn = QPushButton("Gerar distribuicao")
        run_btn.clicked.connect(self.run_solver)
        action_row.addWidget(save_btn)
        action_row.addWidget(run_btn)
        self.export_program_button.clicked.connect(self.export_program_sheet)
        self.export_program_button.setEnabled(False)
        action_row.addWidget(self.export_program_button)
        self.export_cl_txt_button.clicked.connect(self.export_cl_distribution_txt)
        self.export_cl_txt_button.setEnabled(False)
        action_row.addWidget(self.export_cl_txt_button)
        layout.addLayout(action_row)

        self.output_text.setReadOnly(True)
        layout.addWidget(self.output_text)

    def reset_for_operation(self, default_user: str, op_config: Optional[OperationalConfig]) -> None:
        self.user_edit.setText("")
        self.boats_table.setRowCount(0)
        self.demand_table.setRowCount(0)
        self.output_text.clear()
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
        self.troca_check.setChecked(version.troca_turma)
        self.rendidos_edit.setText(str(version.rendidos_m9))
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
        self.boats_table.insertRow(row)
        values = [
            boat.nome if boat else "",
            boat.hora_saida if boat else "",
            boat.rota_fixa if boat else "",
        ]
        for col, value in enumerate(values):
            self.boats_table.setItem(row, col, QTableWidgetItem(value))

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
            troca_turma=self.troca_check.isChecked(),
            rendidos_m9=int(self.rendidos_edit.text().strip() or 0),
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
        try:
            version = self.build_version()
        except ValueError as exc:
            QMessageBox.warning(self, "Distribuicao", str(exc))
            return
        self.parent_window.current_operation, result = self.service.run_version(
            self.parent_window.current_root,
            self.parent_window.current_operation,
            version,
            self.imported_csv_path,
        )
        self.output_text.setPlainText(result.distribution_text)
        self.export_program_button.setEnabled(bool(result.distribution_text.strip()))
        self.export_cl_txt_button.setEnabled(bool(result.distribution_text.strip()))
        if self.version_name in (VERSION_PROGRAMACAO, VERSION_CL):
            self._offer_program_sheet_export(result.distribution_text)
        self.parent_window.reload_operations(select_operation_id=self.parent_window.current_operation.operacao_id)
        self.parent_window.refresh_operation()

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
