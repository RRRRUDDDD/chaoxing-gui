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
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes + ["pystray"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir 模式：二进制文件由 COLLECT 处理
    name="chaoxing-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # console=True 确保 stdin/stdout 管道可用
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="web/public/fav.jpg",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="chaoxing-backend",
)
