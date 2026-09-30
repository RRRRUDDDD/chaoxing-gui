"""Both download entry points reject links before creating a directory."""

import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from api.course_tool_tasks import download_directory
from api.course_tools import CourseTools
from api.fs_policy import reject_links


class FsPolicyTests(unittest.TestCase):
    def test_missing_descendants_are_allowed_without_creating_them(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "new" / "task"
            self.assertEqual(reject_links(target), target.absolute())
            self.assertFalse(target.exists())

    def test_both_callers_reject_symlinks_and_windows_reparse_points(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            original = Path.lstat
            for info in (
                SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0),
                SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400),
            ):
                def linked(path):
                    return info if path == base else original(path)

                for create in (
                    lambda: download_directory(base, "task", create=True),
                    lambda: CourseTools._download_directory(base / "new"),
                ):
                    with self.subTest(info=info, create=create), patch.object(Path, "lstat", linked):
                        with self.assertRaisesRegex(ValueError, "符号链接、目录联接或重解析点"):
                            create()
                self.assertEqual(list(base.iterdir()), [])

    def test_permission_errors_are_not_treated_as_missing_paths(self):
        with patch.object(Path, "lstat", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                reject_links("downloads")
