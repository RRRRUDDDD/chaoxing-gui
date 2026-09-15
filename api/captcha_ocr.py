"""The default ddddocr 1.6.1 text path, without its OpenCV-only features.

Preprocessing and CTC decoding are adapted from ddddocr (MIT); see
resource/licenses/ddddocr-LICENSE.txt. The pinned wheel supplies the unchanged
model and character table. This adapter implements only classification(bytes).
"""

from __future__ import annotations

import ast
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path
import sys


DDDDOCR_VERSION = "1.6.1"
MODEL_NAME = "common_old.onnx"
MODEL_SHA256 = "b8f2ad9cbc1f2e3922a6cb9459e30824e7e2467f3fb4fd61420640e34ea0bf68"
CHARSET_SHA256 = "2c097552127cb5476189e653c0d2ccfd6b6c87425c57f81a01b5770207c33360"


def _load_assets() -> tuple[Path, tuple[str, ...]]:
    # Metadata lookup does not import ddddocr/__init__.py (which requires cv2).
    # copy_metadata in both specs preserves the same lookup in frozen programs.
    distribution = metadata.distribution("ddddocr")
    if distribution.version != DDDDOCR_VERSION:
        raise RuntimeError(f"Captcha assets require ddddocr=={DDDDOCR_VERSION}, found {distribution.version}")
    root = Path(distribution.locate_file("ddddocr"))
    model = root / MODEL_NAME
    with model.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_SHA256:
            raise RuntimeError("Captcha model SHA256 mismatch")

    # Treat the upstream character table as data, never execute package code.
    tree = ast.parse((root / "charsets.py").read_text(encoding="utf-8"))
    charset = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CHARSET_OLD" for target in node.targets
        ):
            charset = ast.literal_eval(node.value)
            break
    if not isinstance(charset, list) or not charset or charset[0] != "" or not all(
        isinstance(char, str) for char in charset
    ):
        raise RuntimeError("Invalid default captcha character table")
    encoded = json.dumps(charset, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != CHARSET_SHA256:
        raise RuntimeError("Captcha character table SHA256 mismatch")
    return model, tuple(charset)


def _preprocess_image(data: bytes):
    import numpy as np
    from PIL import Image

    if not isinstance(data, bytes):
        raise TypeError("Captcha image must be bytes")
    with Image.open(io.BytesIO(data)) as image:
        # Match ddddocr's defaults exactly: resize before grayscale, png_fix=False,
        # float32 in [0, 1]. In particular, do not normalize to [-1, 1].
        width = int(image.width * (64 / image.height))
        resized = image.resize((width, 64), Image.Resampling.LANCZOS).convert("L")
        values = np.array(resized).astype(np.float32) / 255.0
    return values[np.newaxis, np.newaxis, :, :]


def _decode_text(output, charset: tuple[str, ...]) -> str:
    import numpy as np

    if output.ndim == 3:
        scores = output[:, 0, :] if output.shape[1] == 1 else output[0, :, :]
        indices = np.argmax(scores, axis=1)
    else:
        indices = np.atleast_1d(np.argmax(output, axis=-1))
    text = []
    previous = None
    for value in indices:
        index = int(value)
        # Collapse repeated indices before removing blank (0), so a blank can
        # separate two equal letters. Different indices may map to equal text.
        if index != previous and 0 < index < len(charset):
            text.append(charset[index])
        previous = index
    return "".join(text)


class CaptchaOcr:
    """CPU recognition using the original default model and full character set."""

    def __init__(self):
        self.model_path, self.charset = _load_assets()
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self._session = onnxruntime.InferenceSession(
            str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"],
        )
        self._input_name = self._session.get_inputs()[0].name

    def classification(self, image: bytes) -> str:
        values = _preprocess_image(image)
        outputs = self._session.run(None, {self._input_name: values})
        return _decode_text(outputs[0], self.charset)


def check_captcha_ocr(report_path: str | None = None) -> int:
    """Offline diagnostic for both actual executables; no Web or account IO."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        from api.captcha import ocr_init

        engine = ocr_init()
        if engine is None:
            raise RuntimeError("The application's captcha engine could not initialize")
        image = Image.new("RGB", (160, 60), "white")
        ImageDraw.Draw(image).text((8, 4), "1234", font=ImageFont.load_default(size=40), fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        prediction = engine.classification(buffer.getvalue())
        if prediction != "1234":
            raise RuntimeError(f"Captcha self-check expected 1234, received {prediction!r}")
        if any(name.split(".")[0] in {"cv2", "ddddocr"} for name in sys.modules):
            raise RuntimeError("Captcha self-check imported an excluded OCR dependency")
        report = {
            "success": True, "model": MODEL_NAME, "model_sha256": MODEL_SHA256,
            "charset_sha256": CHARSET_SHA256, "characters": len(engine.charset),
            "providers": engine._session.get_providers(), "prediction": prediction,
            "frozen": bool(getattr(sys, "frozen", False)),
        }
    except Exception as exc:
        report = {"success": False, "error": str(exc)}
    if report_path is not None:
        try:
            # The diagnostic caller supplies a fresh output file. Never replace
            # an existing file, and never create application data directories.
            with Path(report_path).open("x", encoding="utf-8") as output:
                json.dump(report, output, ensure_ascii=True, indent=2)
        except OSError as exc:
            report = {"success": False, "error": str(exc)}
    # console=False has no standard streams; use the report file and exit status.
    # Avoid a GUI traceback dialog for a failed diagnostic.
    stream = sys.stdout or sys.stderr
    if stream is not None:
        print(json.dumps(report, ensure_ascii=True), file=stream, flush=True)
    return 0 if report["success"] else 1
