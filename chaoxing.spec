# -*- mode: python ; coding: utf-8 -*-
# Shared analysis inputs live in packaging/spec_common.py.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH) / "packaging"))
from spec_common import datas, hiddenimports, excludes

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports + ["pystray"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="chaoxing-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="web/public/fav.jpg",
)
