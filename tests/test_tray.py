"""Standalone tray exit: confirm running work, close the store, then exit."""

import sys
import unittest
from unittest.mock import Mock, patch

import app as web
from api.task_state import TaskStore


class TrayExitTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.icon = Mock()
        patches = [
            patch.object(web, "task_store", self.store),
            patch.object(web.os, "_exit"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_idle_exit_closes_the_store_before_leaving(self):
        order = []
        self.store.running_count.return_value = 0
        self.icon.stop.side_effect = lambda: order.append("icon")
        self.store.close.side_effect = lambda: order.append("store")
        web.os._exit.side_effect = lambda code: order.append(("exit", code))
        web.tray_exit(self.icon)
        self.assertEqual(order, ["icon", "store", ("exit", 0)])

    @unittest.skipUnless(sys.platform == "win32", "native message box")
    def test_running_tasks_ask_first_and_no_keeps_running(self):
        import ctypes
        self.store.running_count.return_value = 2
        with patch.object(ctypes.windll.user32, "MessageBoxW", return_value=7) as box:
            web.tray_exit(self.icon)
        self.assertIn("2 个任务", box.call_args.args[1])
        self.icon.stop.assert_not_called()
        self.store.close.assert_not_called()
        web.os._exit.assert_not_called()
        with patch.object(ctypes.windll.user32, "MessageBoxW", return_value=6):
            web.tray_exit(self.icon)
        self.store.close.assert_called_once()
        web.os._exit.assert_called_once_with(0)

    def test_store_failure_still_exits(self):
        self.store.running_count.return_value = 0
        self.store.close.side_effect = OSError("disk gone")
        with self.assertRaises(OSError):
            web.tray_exit(self.icon)
        web.os._exit.assert_called_once_with(0)


class RunningCountTests(unittest.TestCase):
    def test_only_live_workers_count(self):
        store = TaskStore()
        self.addCleanup(store.close)
        self.assertEqual(store.running_count(), 0)
        first = store.create("alice", {}, {})
        store.create("bob", {}, {})
        self.assertEqual(store.running_count(), 2)
        store.finish(first, "completed")
        self.assertEqual(store.running_count(), 1)


if __name__ == "__main__":
    unittest.main()
