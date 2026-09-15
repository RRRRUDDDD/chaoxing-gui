# -*- mode: python ; coding: utf-8 -*-
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
# Keep this whitelist in sync with chaoxing-backend.spec; never collect all models.
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
    "pystray",
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
