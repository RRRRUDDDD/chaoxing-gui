"""Default captcha recognition and its frozen-package entry contract."""

import hashlib
import io
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from api import captcha, captcha_ocr as ocr


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def logits(indices, classes=4):
    output = np.full((len(indices), 1, classes), -10, dtype=np.float32)
    for position, index in enumerate(indices):
        output[position, 0, index] = 10
    return output


class CaptchaTensorTests(unittest.TestCase):
    def test_input_is_float32_nchw_with_unit_range(self):
        image = Image.new("L", (3, 64))
        image.paste(128, (1, 0, 2, 64))
        image.paste(255, (2, 0, 3, 64))
        tensor = ocr._preprocess_image(png(image))
        self.assertEqual(tensor.shape, (1, 1, 64, 3))
        self.assertEqual(tensor.dtype, np.float32)
        np.testing.assert_array_equal(tensor[0, 0, :, 0], np.zeros(64))
        np.testing.assert_array_equal(tensor[0, 0, :, 2], np.ones(64))
        self.assertEqual(tensor[0, 0, 0, 1], np.float32(128 / 255))

    def test_resize_preserves_aspect_ratio_using_integer_width(self):
        tensor = ocr._preprocess_image(png(Image.new("RGB", (101, 37), "white")))
        self.assertEqual(tensor.shape, (1, 1, 64, 174))
        self.assertTrue(np.all(tensor == 1))

    def test_default_rgba_behavior_does_not_composite_onto_white(self):
        image = Image.new("RGBA", (4, 64), (0, 0, 0, 0))
        self.assertTrue(np.all(ocr._preprocess_image(png(image)) == 0))

    def test_invalid_image_and_non_bytes_fail(self):
        for value in (b"not-an-image", "image.png", None):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError, OSError)):
                ocr._preprocess_image(value)

    def test_ctc_deduplicates_indices_and_preserves_blank_separated_repeats(self):
        charset = ("", "a", "b", "a")
        self.assertEqual(ocr._decode_text(logits([0, 1, 1, 0, 1, 2, 2, 3]), charset), "aaba")
        self.assertEqual(ocr._decode_text(logits([1, 3]), charset), "aa")
        self.assertEqual(ocr._decode_text(logits([0, 0]), charset), "")

    def test_decoder_accepts_upstream_sequence_layouts(self):
        output = logits([1, 1, 0, 2])
        for layout in (output, output.transpose(1, 0, 2), output[:, 0, :]):
            with self.subTest(shape=layout.shape):
                self.assertEqual(ocr._decode_text(layout, ("", "a", "b", "c")), "ab")
        self.assertEqual(ocr._decode_text(np.array([0, 1, 0]), ("", "a", "b")), "a")


class CaptchaAssetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="chaoxing-captcha-assets-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.assets = self.root / "ddddocr"
        self.assets.mkdir()
        self.model = self.assets / "common_old.onnx"
        self.model.write_bytes(b"synthetic-test-model")
        self.charset = ("", "a", "b")
        self.charset_file = self.assets / "charsets.py"
        self.charset_file.write_text('CHARSET_OLD = ["", "a", "b"]\n', encoding="utf-8")
        self.distribution = SimpleNamespace(version="1.6.1", locate_file=lambda name: self.root / name)
        patches = [
            patch.object(ocr.metadata, "distribution", return_value=self.distribution),
            patch.object(ocr, "MODEL_SHA256", hashlib.sha256(self.model.read_bytes()).hexdigest()),
            patch.object(ocr, "CHARSET_SHA256", hashlib.sha256(b'["","a","b"]').hexdigest()),
        ]
        for current in patches:
            current.start()
            self.addCleanup(current.stop)

    def test_asset_location_uses_distribution_not_current_directory(self):
        model, charset = ocr._load_assets()
        self.assertEqual(model, self.model)
        self.assertEqual(charset, self.charset)

    def test_unverified_package_version_is_rejected(self):
        self.distribution.version = "1.6.2"
        with self.assertRaisesRegex(RuntimeError, "1.6.1"):
            ocr._load_assets()

    def test_missing_and_corrupt_model_fail_before_inference(self):
        self.model.write_bytes(b"wrong-model")
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            ocr._load_assets()
        self.model.unlink()
        with self.assertRaises(FileNotFoundError):
            ocr._load_assets()

    def test_charset_order_and_missing_file_are_checked(self):
        self.charset_file.write_text('CHARSET_OLD = ["", "b", "a"]\n', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            ocr._load_assets()
        self.charset_file.unlink()
        with self.assertRaises(FileNotFoundError):
            ocr._load_assets()

    def test_charset_source_is_parsed_without_executing_it(self):
        marker = self.root / "must-not-be-created"
        self.charset_file.write_text(
            f'__import__("pathlib").Path({str(marker)!r}).touch()\n'
            'CHARSET_OLD = ["", "a", "b"]\n', encoding="utf-8",
        )
        self.assertEqual(ocr._load_assets()[1], self.charset)
        self.assertFalse(marker.exists())


class CaptchaLifecycleTests(unittest.TestCase):
    def test_default_engine_uses_cpu_and_retains_classification_contract(self):
        runtime = Mock()
        session = runtime.InferenceSession.return_value
        session.get_inputs.return_value = [SimpleNamespace(name="image")]
        session.run.return_value = [logits([1, 1, 0, 2])]
        with patch.dict(sys.modules, {"onnxruntime": runtime}), \
                patch.object(ocr, "_load_assets", return_value=(Path("common_old.onnx"), ("", "a", "b", "c"))):
            engine = ocr.CaptchaOcr()
            self.assertEqual(engine.classification(png(Image.new("RGB", (100, 40)))), "ab")
        self.assertEqual(runtime.InferenceSession.call_args.kwargs["providers"], ["CPUExecutionProvider"])
        self.assertEqual(session.run.call_args.args[1]["image"].shape, (1, 1, 64, 160))

    def test_optional_dependencies_or_bad_assets_keep_manual_fallback(self):
        for error in (ImportError("missing onnxruntime"), FileNotFoundError("model"), RuntimeError("wrong version")):
            with self.subTest(error=error), patch.object(captcha, "CaptchaOcr", side_effect=error):
                self.assertIsNone(captcha.ocr_init())

    def test_explicit_none_and_injected_recognizer_do_not_initialize(self):
        with patch.object(captcha, "ocr_init") as initialize:
            disabled = captcha.CxCaptcha("offline-test", "", ocr=None)
            injected = captcha.CxCaptcha("offline-test", "", ocr=Mock())
            self.addCleanup(disabled.s.close)
            self.addCleanup(injected.s.close)
            with self.assertRaises(RuntimeError):
                disabled.recognition(b"synthetic-image")
            injected.ocr.classification.return_value = "1234"
            self.assertEqual(injected.recognition(b"synthetic-image"), "1234")
            injected.ocr.classification.assert_called_once_with(b"synthetic-image")
            initialize.assert_not_called()

    def test_recognition_failure_never_submits(self):
        client = captcha.CxCaptcha("offline-test", "", ocr=Mock())
        self.addCleanup(client.s.close)
        client.ocr.classification.side_effect = ValueError("bad image")
        with patch.object(client, "getCaptcha", return_value=b"image"), patch.object(client, "submitCaptcha") as submit:
            self.assertFalse(client.try_pass())
            submit.assert_not_called()

    def test_self_check_runs_before_web_imports_and_business_initialization(self):
        entry = str(Path(__file__).resolve().parents[1] / "app.py")
        for code in (0, 1):
            with self.subTest(code=code), patch.object(sys, "argv", [entry, "--check-captcha-ocr"]), \
                    patch.object(ocr, "check_captcha_ocr", return_value=code) as check, \
                    patch.dict(sys.modules, {"flask": None}), self.assertRaises(SystemExit) as stopped:
                runpy.run_path(entry, run_name="__main__")
            self.assertEqual(stopped.exception.code, code)
            check.assert_called_once_with(None)

    def test_invalid_self_check_arguments_do_not_start_application(self):
        entry = str(Path(__file__).resolve().parents[1] / "app.py")
        with patch.object(sys, "argv", [entry, "--check-captcha-ocr", "report.json", "unexpected"]), \
                patch.dict(sys.modules, {"flask": None}), self.assertRaises(SystemExit) as stopped:
            runpy.run_path(entry, run_name="__main__")
        self.assertEqual(stopped.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
