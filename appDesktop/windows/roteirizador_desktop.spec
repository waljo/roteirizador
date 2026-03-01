# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path.cwd()
app_root = project_root / "appDesktop"

datas = [
    (str(project_root / "distplat.json"), "."),
    (str(project_root / "gangway.json"), "."),
    (str(project_root / "velocidades.txt"), "."),
    (str(project_root / "geradorPlanilhaProgramação" / "criarTabela6.py"), "geradorPlanilhaProgramação"),
]

hiddenimports = [
    "solver",
]

a = Analysis(
    [str(app_root / "roteirizador_desktop_main.py")],
    pathex=[str(project_root), str(app_root)],
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
