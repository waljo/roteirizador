from __future__ import annotations

import re
import sys
from pathlib import Path


def check_patterns(source: str) -> list[str]:
    errors: list[str] = []

    required_literals = [
        'LAYOUT_SPEC_VERSION = "1.1.0"',
        "HELP_SECTION_MAX_HEIGHT = 220",
        "BUTTON_GRID_SPACING = 6",
        "USE_COLLAPSIBLE_SPLITTERS = False",
        "self.scroll_area.setMaximumHeight(HELP_SECTION_MAX_HEIGHT)",
        "self.scroll_area.setMaximumHeight(0)",
        "boats_btns = QHBoxLayout()",
        "demand_btns = QHBoxLayout()",
        "boats_layout.setStretch(1, 1)",
        "demand_layout.setStretch(1, 1)",
        'add_boat = QPushButton("Add barco")',
        'remove_boat = QPushButton("Exc. barco")',
        'add_demand = QPushButton("Add linha")',
        'remove_demand = QPushButton("Exc. linha")',
        'import_csv = QPushButton("Imp. csv")',
        'import_pdf = QPushButton("Imp. Extrato pdf")',
        'export_csv = QPushButton("Exp. csv")',
    ]
    for literal in required_literals:
        if literal not in source:
            errors.append(f"Trecho obrigatorio ausente: {literal}")

    splitter_calls = re.findall(r"setChildrenCollapsible\(USE_COLLAPSIBLE_SPLITTERS\)", source)
    if len(splitter_calls) < 3:
        errors.append("Esperado no minimo 3 splitters com USE_COLLAPSIBLE_SPLITTERS.")

    return errors


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent
    ui_path = repo_root / "appDesktop" / "roteirizador_desktop" / "ui.py"
    if not ui_path.exists():
        print(f"[ERRO] Arquivo nao encontrado: {ui_path}")
        return 1

    source = ui_path.read_text(encoding="utf-8")
    errors = check_patterns(source)
    if errors:
        print("[ERRO] Validacao de layout falhou:")
        for item in errors:
            print(f"- {item}")
        return 1

    print("[OK] Layout baseline validado com sucesso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
