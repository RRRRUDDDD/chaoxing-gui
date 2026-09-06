import threading
import unittest
from unittest.mock import patch
from uuid import UUID

from api.task_state import TaskAlreadyRunning, TaskStore


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.store = TaskStore(
            ttl_seconds=10,
            max_logs=3,
            max_message_chars=12,
            clock=lambda: self.now,
            wall_time=lambda: 1000 + self.now,
        )
        self.addCleanup(self.store.close)

    def create(self, account="alice"):
        return self.store.create(account, {"progress": 0}, {"courses": []})

    def test_account_reservation_is_atomic_and_ids_are_uuids(self):
        barrier = threading.Barrier(8)
        ids = []
        conflicts = []
        results_lock = threading.Lock()

        def reserve():
            barrier.wait(timeout=3)
            try:
                task_id = self.create()
                with results_lock:
                    ids.append(task_id)
            except TaskAlreadyRunning as exc:
                with results_lock:
                    conflicts.append(exc.task_id)

        threads = [threading.Thread(target=reserve) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(ids), 1)
        self.assertEqual(UUID(ids[0]).version, 4)
        self.assertEqual(conflicts, [ids[0]] * 7)
        self.assertNotEqual(self.create("bob"), ids[0])
        self.store.finish(ids[0], "partial")
        self.assertNotEqual(self.create(), ids[0])

    def test_logs_are_isolated_bounded_and_reads_do_not_consume(self):
        alice = self.create()
        bob = self.create("bob")
        for index in range(5):
            self.store.append_log(alice, f"alice-{index}", timestamp=index)
            self.store.append_log(bob, f"bob-{index}")
        result = self.store.read_logs(alice)
        self.assertEqual([item["seq"] for item in result["data"]], [3, 4, 5])
        self.assertEqual([item["message"] for item in result["data"]], ["alice-2", "alice-3", "alice-4"])
        self.assertEqual(result["data"][-1]["timestamp"], 4)
        self.assertEqual(result["next_cursor"], 5)
        self.assertTrue(result["truncated"])
        self.assertEqual(self.store.read_logs(alice), result)
        self.assertTrue(all(item["message"].startswith("bob-") for item in self.store.read_logs(bob)["data"]))

    def test_cursor_reports_gaps_and_only_new_records(self):
        task_id = self.create()
        for index in range(5):
            self.store.append_log(task_id, str(index))
        recovered = self.store.read_logs(task_id, after=2)
        self.assertFalse(recovered["truncated"])
        self.assertEqual([item["seq"] for item in recovered["data"]], [3, 4, 5])
        self.store.append_log(task_id, "new")
        result = self.store.read_logs(task_id, after=recovered["next_cursor"])
        self.assertEqual([item["message"] for item in result["data"]], ["new"])
        self.assertEqual(self.store.read_logs(task_id, after=6)["data"], [])
        self.assertEqual(self.store.read_logs(task_id, after=100)["next_cursor"], 100)
        for invalid in (-1, 1.1, True, "1", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.read_logs(task_id, after=invalid)

    def test_long_messages_are_truncated_and_blank_messages_are_ignored(self):
        task_id = self.create()
        self.store.append_log(task_id, " ")
        self.store.append_log(task_id, "x" * 100, level="error", timestamp=12.5)
        log = self.store.read_logs(task_id)["data"][0]
        self.assertEqual(len(log["message"]), 12)
        self.assertTrue(log["message"].endswith("…"))
        self.assertEqual(log["seq"], 1)
        self.assertEqual(log["level"], "error")
        self.assertEqual(log["timestamp"], 12.5)

    def test_snapshots_do_not_expose_mutable_store_state(self):
        details = {"courses": [{"chapters": []}]}
        task_id = self.store.create("alice", {"stats": {"count": 0}}, details)
        details["courses"].clear()
        snapshot = self.store.get_details(task_id)
        snapshot["courses"][0]["chapters"].append("outside mutation")
        self.assertEqual(self.store.get_details(task_id)["courses"], [{"chapters": []}])
        status = self.store.get_status(task_id)
        status["stats"]["count"] = 99
        self.assertEqual(self.store.get_status(task_id)["stats"]["count"], 0)

    def test_edit_serializes_concurrent_progress_updates(self):
        task_id = self.create()

        def update():
            for _ in range(100):
                with self.store.edit(task_id) as task:
                    task.status["progress"] += 1

        threads = [threading.Thread(target=update) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.store.get_status(task_id)["progress"], 600)

    def test_terminal_ttl_removes_status_details_and_logs_together(self):
        task_id = self.create()
        other = self.create("bob")
        self.store.append_log(task_id, "before end")
        self.now += 100
        self.assertEqual(self.store.get_status(task_id)["status"], "running")
        self.store.finish(task_id, "error", error="failed")
        self.now += 9
        self.assertEqual(self.store.get_status(task_id)["error"], "failed")
        self.assertEqual(len(self.store.read_logs(task_id)["data"]), 1)
        self.store.finish(task_id, "completed")
        self.now += 1
        for read in (self.store.get_status, self.store.get_details, self.store.read_logs):
            with self.assertRaises(KeyError):
                read(task_id)
        self.assertIsNone(self.store.append_log(task_id, "late"))
        self.assertEqual(self.store.get_status(other)["status"], "running")

    def test_released_account_is_not_removed_when_old_record_expires(self):
        old = self.create()
        self.store.finish(old, "completed")
        current = self.create()
        self.now += 10
        self.assertEqual(self.store.cleanup(), 1)
        with self.assertRaises(TaskAlreadyRunning) as conflict:
            self.create()
        self.assertEqual(conflict.exception.task_id, current)

    def test_finish_freezes_log_tail_and_clears_active_jobs(self):
        task_id = self.create()
        with self.store.edit(task_id) as task:
            task.details["active_jobs"] = {"job": {"progress": 100}}
        self.store.append_log(task_id, "last log")
        self.store.finish(task_id, "completed")
        self.assertIsNone(self.store.append_log(task_id, "after finish"))
        self.assertEqual(self.store.read_logs(task_id)["next_cursor"], 1)
        self.assertEqual(self.store.get_details(task_id)["active_jobs"], {})

    def test_only_one_cleanup_thread_is_created_for_many_tasks(self):
        store = TaskStore(cleanup_interval=0.01)
        self.addCleanup(store.close)
        with patch("api.task_state.threading.Thread", wraps=threading.Thread) as threads:
            for index in range(12):
                task_id = store.create(str(index), {}, {})
                store.finish(task_id, "completed")
            self.assertEqual(threads.call_count, 1)
        store.close()
        self.assertFalse(store._cleaner.is_alive())

    def test_cleanup_thread_start_failure_does_not_reserve_account(self):
        store = TaskStore(cleanup_interval=0.01)
        self.addCleanup(store.close)
        with patch("api.task_state.threading.Thread.start", side_effect=RuntimeError("cannot start")):
            with self.assertRaises(RuntimeError):
                store.create("alice", {}, {})
        task_id = store.create("alice", {}, {})
        self.assertEqual(store.get_status(task_id)["status"], "running")


if __name__ == "__main__":
    unittest.main()
