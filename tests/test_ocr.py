import os
import tempfile
import threading
import unittest
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

from api import decode, vision_ocr as vision
from api.answer import _apply_ocr_to_title_if_needed


REMOTE_CONFIG = {"provider": "openai", "key": "offline-test-key", "model": "offline-model"}
IMAGE_URL = "https://p.ananas.chaoxing.com/question.png"


def image_response(content=b"offline-image"):
    return Mock(status_code=200, content=content)


class OCRConfigTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config_path = patch.object(vision, "OCR_CONFIG_PATH", "missing-test-ocr-config.ini")
        self.config_path.start()
        self.addCleanup(self.config_path.stop)

    def test_empty_explicit_config_disables_environment_and_restores_outer_config(self):
        with patch.dict(os.environ, {
            "CHAOXING_VISION_OCR_PROVIDER": "openai",
            "CHAOXING_VISION_OCR_KEY": "environment-key",
            "CHAOXING_OCR_ENDPOINT": "https://offline.invalid/http-ocr",
        }):
            with vision.ocr_context(None):
                self.assertTrue(vision.is_vision_ocr_enabled())
                with vision.ocr_context({}):
                    self.assertFalse(vision.is_vision_ocr_enabled())
                    self.assertEqual(vision.get_ocr_config()["http_endpoint"], "")
                self.assertEqual(vision.get_ocr_config()["api_key"], "environment-key")

    def test_cli_config_file_is_read_with_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text("[ocr]\nprovider=openai\nkey=file-key\nmodel=file-model\n", encoding="utf8")
            with patch.object(vision, "OCR_CONFIG_PATH", str(path)), patch.dict(os.environ, {
                "CHAOXING_VISION_OCR_MODEL": "environment-model",
            }), vision.ocr_context(None):
                config = vision.get_ocr_config()
                self.assertEqual(config["api_key"], "file-key")
                self.assertEqual(config["model"], "environment-model")

    def test_concurrent_contexts_and_copied_thread_contexts_are_isolated(self):
        barrier = threading.Barrier(2)

        def task(model):
            source = dict(REMOTE_CONFIG, model=model)
            with vision.ocr_context(source):
                source["model"] = "mutated-after-binding"
                barrier.wait(5)
                result = vision.get_ocr_config()
                result["model"] = "mutated-return-value"
                return vision.get_ocr_config()["model"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(task, ("first", "second"))), ["first", "second"])
            with vision.ocr_context(REMOTE_CONFIG):
                config = pool.submit(copy_context().run, vision.get_ocr_config).result(5)
                self.assertEqual(config["model"], "offline-model")
        self.assertFalse(vision.is_vision_ocr_enabled())

    def test_remote_empty_success_is_distinct_from_http_failure(self):
        empty = Mock(status_code=200)
        empty.json.return_value = {"choices": [{"message": {"content": "[空]"}}]}
        failure = Mock(status_code=503, text="offline failure")
        with vision.ocr_context(REMOTE_CONFIG), patch("api.vision_ocr.requests.post", side_effect=[empty, failure]):
            result = vision.vision_ocr_result(b"image")
            self.assertTrue(result.success)
            self.assertEqual(result.text, "")
            self.assertFalse(vision.vision_ocr_result(b"image").success)
        empty.close.assert_called_once()
        failure.close.assert_called_once()


class OCRPipelineTests(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.session = Mock()
        self.session.get.return_value = image_response()
        self.result = Mock(return_value=vision.OCRResult("recognized", success=True))
        patches = [
            patch.object(decode, "_OCR_URL_CACHE", OrderedDict()),
            patch.object(decode, "_OCR_CONTENT_CACHE", OrderedDict()),
            patch.object(decode.time, "monotonic", side_effect=lambda: self.clock[0]),
            patch.object(decode, "get_current_session", return_value=self.session),
            patch.object(decode, "vision_ocr_result", self.result),
            patch.object(decode, "_PADDLE_OCR_ENGINE", None),
            patch.object(decode, "_PADDLE_OCR_INITIALIZED", False),
            patch.object(decode, "_PADDLE_OCR_DEVICE", None),
            patch.object(decode, "_PADDLE_OCR_RETRY_AT", 0.0),
            patch.dict(os.environ, {}, clear=True),
        ]
        for current in patches:
            current.start()
            self.addCleanup(current.stop)
        self.context = vision.ocr_context(REMOTE_CONFIG)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_url_and_content_cache_reuse_recognition_and_borrow_account_session(self):
        with patch("api.decode.requests.Session") as new_session:
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "recognized")
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "recognized")
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL + "?same-image=1"), "recognized")
        self.assertEqual(self.session.get.call_count, 2)
        self.result.assert_called_once()
        new_session.assert_not_called()
        self.session.close.assert_not_called()
        self.session.cookies.update.assert_not_called()

    def test_all_remote_engine_settings_are_part_of_cache_identity(self):
        configs = [REMOTE_CONFIG, dict(REMOTE_CONFIG, model="another-model"),
                   dict(REMOTE_CONFIG, key="another-key"),
                   dict(REMOTE_CONFIG, endpoint="https://offline.invalid/another-endpoint"),
                   dict(REMOTE_CONFIG, prompt="another prompt")]
        for config in configs:
            with vision.ocr_context(config):
                self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "recognized")
        self.assertEqual(self.result.call_count, len(configs))

    def test_url_cache_does_not_cross_account_sessions(self):
        second_session = Mock()
        second_session.get.return_value = image_response(b"second-account-image")
        self.result.side_effect = [vision.OCRResult("first", True), vision.OCRResult("second", True)]
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "first")
        with patch.object(decode, "get_current_session", return_value=second_session):
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "second")
        self.assertEqual(self.result.call_count, 2)

    def test_independent_download_closes_its_session_without_loading_cookies(self):
        with patch.object(decode, "get_current_session", return_value=None), \
                patch("api.decode.requests.Session", return_value=self.session), \
                patch("api.cookies.use_cookies") as cookies:
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "recognized")
        self.session.close.assert_called_once()
        cookies.assert_not_called()
        self.session.cookies.update.assert_not_called()

    def test_independent_session_is_closed_after_download_failure(self):
        self.session.get.side_effect = requests.Timeout("offline timeout")
        with patch.object(decode, "get_current_session", return_value=None), \
                patch("api.decode.requests.Session", return_value=self.session):
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.session.close.assert_called_once()

    def test_download_failure_has_short_retry_window(self):
        self.session.get.side_effect = [requests.Timeout("offline timeout"), image_response()]
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.clock[0] += 1
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.assertEqual(self.session.get.call_count, 1)
        self.clock[0] += decode._OCR_FAILURE_TTL
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "recognized")
        self.assertEqual(self.session.get.call_count, 2)

    def test_remote_failure_is_retried_without_permanent_empty_cache(self):
        self.result.side_effect = [vision.OCRResult(), vision.OCRResult("recovered", True)]
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.assertEqual(self.result.call_count, 1)
        self.clock[0] += decode._OCR_FAILURE_TTL + 1
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "recovered")

    def test_successful_empty_recognition_has_finite_longer_cache_window(self):
        self.result.return_value = vision.OCRResult("", True)
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.clock[0] += decode._OCR_FAILURE_TTL + 1
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.assertEqual(self.result.call_count, 1)
        self.clock[0] += decode._OCR_EMPTY_TTL
        self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.assertEqual(self.result.call_count, 2)

    def test_concurrent_requests_for_one_image_recognize_once(self):
        barrier = threading.Barrier(8)

        def recognize(_):
            with vision.ocr_context(REMOTE_CONFIG):
                barrier.wait(5)
                return decode._ocr_image_to_text(IMAGE_URL)

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(recognize, range(8))), ["recognized"] * 8)
        self.session.get.assert_called_once()
        self.result.assert_called_once()

    def test_cache_capacity_is_bounded(self):
        with patch.object(decode, "_OCR_CACHE_MAXSIZE", 3):
            for index in range(8):
                self.session.get.return_value = image_response(str(index).encode())
                decode._ocr_image_to_text(IMAGE_URL + str(index))
            self.assertLessEqual(len(decode._OCR_URL_CACHE), 3)
            self.assertLessEqual(len(decode._OCR_CONTENT_CACHE), 3)

    def test_paddle_initialization_failure_backs_off_even_for_forced_device(self):
        with patch.object(decode, "_import_paddle_ocr_class", side_effect=ImportError("offline missing paddle")) as load:
            self.assertIsNone(decode._init_paddle_ocr())
            self.assertIsNone(decode._init_paddle_ocr())
            self.assertIsNone(decode._init_paddle_ocr(preferred_device="cpu"))
            self.assertEqual(load.call_count, 1)
            self.clock[0] += decode._PADDLE_OCR_RETRY_SECONDS + 1
            self.assertIsNone(decode._init_paddle_ocr())
            self.assertEqual(load.call_count, 2)

    def test_concurrent_paddle_initialization_constructs_only_one_engine(self):
        engine = object()
        constructor = Mock(return_value=engine)
        with patch.object(decode, "_import_paddle_ocr_class", return_value=constructor):
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertTrue(all(result is engine for result in pool.map(lambda _: decode._init_paddle_ocr(), range(8))))
        constructor.assert_called_once()

    def test_explicitly_disabled_ocr_does_not_download_or_initialize(self):
        with vision.ocr_context({}), patch.object(decode, "_init_paddle_ocr") as initialize:
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.session.get.assert_not_called()
        initialize.assert_not_called()

    def test_local_inference_cleans_temporary_image_and_caches_text(self):
        engine = Mock()
        engine.predict.return_value = [{"rec_texts": ["x=1", "y=2"]}]
        with vision.ocr_context({"enable_local": True}), \
                patch.object(decode, "_init_paddle_ocr", return_value=engine), \
                patch.object(decode, "_preprocess_image_for_ocr", return_value=b"processed"):
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "x=1 y=2")
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "x=1 y=2")
        engine.predict.assert_called_once()
        self.assertFalse(Path(engine.predict.call_args.args[0]).exists())
        self.result.assert_not_called()

    def test_local_gpu_inference_can_fall_back_to_cpu(self):
        gpu, cpu = Mock(), Mock()
        gpu.predict.side_effect = RuntimeError("offline GPU failure")
        cpu.predict.return_value = [{"rec_texts": ["CPU answer"]}]
        with vision.ocr_context({"enable_local": True}), \
                patch.object(decode, "_PADDLE_OCR_DEVICE", "gpu"), \
                patch.object(decode, "_init_paddle_ocr", side_effect=[gpu, cpu]) as initialize, \
                patch.object(decode, "_preprocess_image_for_ocr", return_value=b"processed"):
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "CPU answer")
        self.assertEqual(initialize.call_count, 2)
        self.assertEqual(initialize.call_args.kwargs, {"preferred_device": "cpu"})
        self.assertFalse(Path(cpu.predict.call_args.args[0]).exists())

    def test_parse_then_query_does_not_repeat_failed_model_initialization(self):
        title = BeautifulSoup(f'<div>formula<img src="{IMAGE_URL}"></div>', "html.parser").div
        with vision.ocr_context({"enable_local": True}), \
                patch.object(decode, "_import_paddle_ocr_class", side_effect=ImportError("offline missing paddle")) as load:
            info = {"title": decode._extract_title(title)}
            _apply_ocr_to_title_if_needed(info)
            decode._ocr_image_to_text(IMAGE_URL + "?next-image=1")
            self.assertEqual(load.call_count, 1)
            self.assertIn(IMAGE_URL, info["title"])
            self.assertEqual(self.session.get.call_count, 2)

    def test_http_fallback_empty_response_does_not_hide_primary_failure(self):
        self.result.return_value = vision.OCRResult()
        response = Mock(status_code=200)
        response.json.return_value = {"text": ""}
        with vision.ocr_context(dict(REMOTE_CONFIG, http_endpoint="https://offline.invalid/ocr")), \
                patch("api.decode.requests.post", return_value=response):
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
            self.clock[0] += decode._OCR_FAILURE_TTL + 1
            self.assertEqual(decode._ocr_image_to_text(IMAGE_URL), "")
        self.assertEqual(self.result.call_count, 2)
        self.assertEqual(response.close.call_count, 2)


class OCRParsingCompatibilityTests(unittest.TestCase):
    def test_paddle_two_and_three_result_formats(self):
        modern = [{"rec_texts": [" x=1 ", "y=2"]}]
        legacy = [[[[[0, 0], [1, 1]], ("x=1", 0.99)], [[[0, 0], [1, 1]], ("y=2", 0.98)]]]
        self.assertEqual(decode._parse_paddle_ocr_result(modern), ["x=1", "y=2"])
        self.assertEqual(decode._parse_paddle_ocr_result(legacy), ["x=1", "y=2"])

    def test_title_and_choice_parsing_preserve_image_identity_after_failure(self):
        title = BeautifulSoup(f'<div>formula<img src="{IMAGE_URL}"></div>', "html.parser").div
        with patch.object(decode, "_ocr_image_to_text", return_value=""):
            self.assertEqual(decode._extract_title(title), f'formula<img src="{IMAGE_URL}">')
        info = {"title": f'formula<img src="{IMAGE_URL}">'}
        with patch("api.answer._ocr_image_to_text", return_value=""):
            _apply_ocr_to_title_if_needed(info)
        self.assertIn(IMAGE_URL, info["title"])
        choice = BeautifulSoup('<li aria-label=" A. answer 选择">ignored text</li>', "html.parser").li
        self.assertEqual(decode._extract_choices(choice), "A. answer")

    def test_question_option_labels_and_multiple_answer_fields_are_preserved(self):
        html = '''<div data="42"><div class="TiMu" data="0">
        <div class="Zy_TItle">Question</div><ul>
        <li aria-label="B. second 选择"></li><li aria-label="A. first 选择"></li>
        </ul><input name="answer42_0" value=""><input name="answer42_1" value="">
        </div></div>'''
        question = decode._process_question(BeautifulSoup(html, "html.parser").div)
        self.assertEqual(question["type"], "single")
        self.assertEqual(question["options"], "A. first\nB. second")
        self.assertEqual(question["answerField"], {
            "answer42": "", "answertype42": "0", "answer42_0": "", "answer42_1": "",
        })


if __name__ == "__main__":
    unittest.main()
