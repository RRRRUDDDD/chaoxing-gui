import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from api.answer import AI, CacheDAO, Tiku


class _PauseReaderAfterUnlock:
    """Force a writer between a cold read's unlock and any later publication."""

    def __init__(self, unlocked, written):
        self.lock = threading.RLock()
        self.local = threading.local()
        self.unlocked = unlocked
        self.written = written
        self.paused = False

    def __enter__(self):
        self.lock.acquire()
        self.local.depth = getattr(self.local, "depth", 0) + 1
        return self

    def __exit__(self, *args):
        self.local.depth -= 1
        self.lock.release()
        if (threading.current_thread().name == "cold-cache-reader"
                and self.local.depth == 0 and not self.paused):
            self.paused = True
            self.unlocked.set()
            if not self.written.wait(5):
                raise AssertionError("writer did not finish after reader unlocked")


class AnswerCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "answers.json"
        self.cache = CacheDAO(str(self.path))
        self.shared = patch.object(CacheDAO, "get_shared", return_value=self.cache)
        self.shared.start()
        self.addCleanup(self.shared.stop)

    def test_option_order_and_question_type_do_not_share_letter_answers(self):
        provider = AI()
        provider._query = Mock(side_effect=["A", "B", "A\nB"])
        first = {"title": "Which is four?", "type": "single", "options": "A. 4\nB. 5"}
        swapped = dict(first, options="A. 5\nB. 4")
        multiple = dict(first, type="multiple")
        self.assertEqual(provider.query(first), "A")
        self.assertEqual(provider.query(swapped), "B")
        self.assertEqual(provider.query(multiple), "A\nB")
        self.assertEqual(provider.query(first), "A")
        self.assertEqual(provider._query.call_count, 3)

    def test_legacy_title_only_entries_are_not_reused(self):
        self.path.write_text(json.dumps({"Question": "wrong legacy answer"}), encoding="utf8")
        provider = AI()
        provider._query = Mock(return_value="new answer")
        question = {"title": "Question", "type": "shortanswer", "options": ""}
        self.assertEqual(provider.query(question), "new answer")
        provider._query.assert_called_once()
        self.assertTrue(self.cache.flush())
        keys = json.loads(self.path.read_text(encoding="utf8"))
        self.assertTrue(any(key.startswith("question:v2:") for key in keys))

    def test_key_normalizes_whitespace_and_unicode_without_reordering_options(self):
        provider = AI()
        provider._query = Mock(return_value="A")
        first = {"title": "  cafe\u0301   question  ", "type": "single", "options": [" A.  one ", " B. two"]}
        second = {"title": "café question", "type": "single", "options": "A. one\nB. two"}
        self.assertEqual(provider.query(first), "A")
        self.assertEqual(provider.query(second), "A")
        provider._query.assert_called_once()

    def test_significant_leading_numbers_remain_part_of_cache_identity(self):
        provider = AI()
        provider._query = Mock(side_effect=["4", "5"])
        first = {"title": "2+2=?", "type": "completion", "options": ""}
        second = dict(first, title="3+2=?")
        self.assertEqual(provider.query(first), "4")
        self.assertEqual(provider.query(second), "5")
        self.assertEqual(provider.query(first), "4")
        self.assertEqual(first["title"], "2+2=?")
        self.assertEqual(provider._query.call_count, 2)

    def test_cold_read_cannot_overwrite_concurrent_update(self):
        self.path.write_text('{"existing": "old"}', encoding="utf8")
        unlocked, written = threading.Event(), threading.Event()
        self.cache._lock = _PauseReaderAfterUnlock(unlocked, written)
        errors = []

        def reader():
            try:
                self.assertEqual(self.cache.get_cache("existing"), "old")
            except BaseException as exc:
                errors.append(exc)

        def writer():
            try:
                if not unlocked.wait(5):
                    raise AssertionError("cold reader never unlocked")
                self.cache.add_cache("new", "answer")
            except BaseException as exc:
                errors.append(exc)
            finally:
                written.set()

        threads = [threading.Thread(target=reader, name="cold-cache-reader"),
                   threading.Thread(target=writer)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(6)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(self.cache.get_cache("new"), "answer")
        self.assertTrue(self.cache.flush())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf8"))["new"], "answer")

    def test_updates_flush_in_batches_and_explicit_flush_is_atomic(self):
        cache = CacheDAO(str(self.path), flush_every=4)
        with patch("api.answer.json.dump", wraps=json.dump) as dump:
            for index in range(3):
                cache.add_cache(str(index), "answer")
            self.assertEqual(dump.call_count, 0)
            self.assertTrue(cache.flush())
            self.assertEqual(dump.call_count, 1)
            for index in range(3, 7):
                cache.add_cache(str(index), "answer")
            self.assertEqual(dump.call_count, 2)
            self.assertTrue(cache.flush())
            self.assertEqual(dump.call_count, 2)
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf8"))), 7)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_failed_replace_preserves_previous_file_and_retries_dirty_data(self):
        self.cache.add_cache("old", "answer")
        self.assertTrue(self.cache.flush())
        self.cache.add_cache("new", "answer")
        with patch("api.answer.os.replace", side_effect=OSError("disk temporarily unavailable")):
            self.assertFalse(self.cache.flush())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf8")), {"old": "answer"})
        self.assertEqual(self.cache.get_cache("new"), "answer")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertTrue(self.cache.flush())
        self.assertIn("new", json.loads(self.path.read_text(encoding="utf8")))

    def test_concurrent_updates_all_reach_disk(self):
        def add_batch(worker):
            for index in range(12):
                self.cache.add_cache(f"{worker}:{index}", "answer")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(add_batch, range(8)))
        self.assertTrue(self.cache.flush())
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf8"))), 96)

    def test_tiku_close_flushes_and_closes_owned_resources_once(self):
        provider = Tiku()
        provider._cache_dao = self.cache
        provider.client = Mock()
        provider._httpx_client = Mock()
        provider._session = Mock()
        clients = [provider.client, provider._httpx_client, provider._session]
        self.cache.add_cache("question", "answer")
        provider.close()
        provider.close()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf8")), {"question": "answer"})
        for client in clients:
            client.close.assert_called_once()

    def test_empty_answer_is_not_cached(self):
        provider = AI()
        provider._query = Mock(side_effect=["  ", "answer"])
        question = {"title": "Question", "type": "completion", "options": ""}
        self.assertIsNone(provider.query(question))
        self.assertEqual(provider.query(question), "answer")
        self.assertEqual(provider._query.call_count, 2)

    def test_tiku_releases_clients_even_if_flush_raises(self):
        provider = Tiku()
        provider._cache_dao = self.cache
        provider._session = Mock()
        session = provider._session
        with patch.object(self.cache, "flush", side_effect=OSError("offline disk failure")):
            self.assertFalse(provider.close())
        session.close.assert_called_once()

    def test_tiku_close_flushes_shared_cache_even_without_queries(self):
        self.cache.add_cache("shared-question", "answer")
        self.assertTrue(Tiku().close())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf8")), {"shared-question": "answer"})


if __name__ == "__main__":
    unittest.main()
