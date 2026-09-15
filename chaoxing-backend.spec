# -*- mode: python ; coding: utf-8 -*-
# Electron 后端专用 spec (独立 exe 版本见 chaoxing.spec，注意同步 datas/hiddenimports)
# 主要差异：console=True (支持 stdin/stdout 管道), 移除 pystray, name=chaoxing-backend
from importlib.metadata import distribution
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

datas = [
    ("web/dist", "web/dist"),
    ("resource", "resource"),
    ("config.ini.example", "."),
    ("fav.jpg", "."),
    ("web/public/fav.jpg", "."),
]
# The adapter reads assets without importing ddddocr (and therefore OpenCV).
# Keep this whitelist in sync with chaoxing.spec; never collect all models.
captcha_assets = distribution("ddddocr")
if captcha_assets.version != "1.6.1":
    raise SystemExit("Captcha packaging requires ddddocr==1.6.1")
datas += [
    (str(captcha_assets.locate_file("ddddocr/" + name)), "ddddocr")
    for name in ("common_old.onnx", "charsets.py")
]
datas += copy_metadata("ddddocr")  # Runtime version/resource lookup and MIT license.

hiddenimports = [
    "flask_cors",
    "loguru",
    "pyaes",
    "bs4",
    "lxml",
    "openai",
    "httpx",
    "onnxruntime",
    "PIL",
    "numpy",
    "tqdm",
    "fontTools",
    "requests",
    "urllib3",
    # 移除 pystray - Electron 无头模式不需要托盘图标
]
hiddenimports += collect_submodules("api")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["ddddocr", "cv2", "paddle", "paddleocr", "paddlepaddle", "paddlex", "PaddleOCR", "celery"],
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
