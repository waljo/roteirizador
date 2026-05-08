# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path.cwd()
app_root = project_root / "appDesktop"

datas = [
    (str(app_root / "resources" / "distplat.json"), "resources"),
    (str(app_root / "resources" / "gangway.json"), "resources"),
    (str(app_root / "resources" / "velocidades.txt"), "resources"),
    (
        str(app_root / "resources" / "geradorPlanilhaProgramação" / "criarTabela6.py"),
        "resources/geradorPlanilhaProgramação",
    ),
]

hiddenimports = [
    "solver",
]

a = Analysis(
    [str(app_root / "roteirizador_desktop_main.py")],
    pathex=[str(app_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RoteirizadorDesktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
