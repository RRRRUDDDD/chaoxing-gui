"""Shared PyInstaller analysis inputs; imported by both root spec files."""

from importlib.metadata import distribution
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(__file__).resolve().parent.parent

datas = [(str(ROOT / source), target) for source, target in (
    ("web/dist", "web/dist"),
    ("resource", "resource"),
    ("config.ini.example", "."),
    ("fav.jpg", "."),
)]
# Read only the verified model/table; do not import ddddocr or collect beta models.
captcha_assets = distribution("ddddocr")
if captcha_assets.version != "1.6.1":
    raise SystemExit("Captcha packaging requires ddddocr==1.6.1")
datas += [(str(captcha_assets.locate_file("ddddocr/" + name)), "ddddocr")
          for name in ("common_old.onnx", "charsets.py")]
datas += copy_metadata("ddddocr")

hiddenimports = [
    "flask_cors", "loguru", "pyaes", "bs4", "lxml", "onnxruntime", "PIL", "numpy",
    "tqdm", "fontTools", "requests", "urllib3",
] + collect_submodules("api")

# Do not allow optional packages in the builder environment to leak into the app.
excludes = [
    "ddddocr", "cv2", "paddle", "paddleocr", "paddlepaddle", "paddlex", "PaddleOCR", "celery",
    "openai", "httpx", "pydantic", "pydantic_core", "anyio", "jiter", "pygments", "websockets",
]
