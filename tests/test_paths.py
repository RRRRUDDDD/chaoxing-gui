"""Tests for api.paths data_dir resolution (the one production module with no dedicated test)."""

import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from api import paths


class DataDirTests(unittest.TestCase):
    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as override:
            with patch.dict(os.environ, {"CHAOXING_DATA_DIR": override}):
                with patch.object(sys, "frozen", False, create=True):
                    self.assertEqual(paths.data_dir(), override)

    def test_frozen_uses_executable_dir(self):
        # tests/__init__ sets CHAOXING_DATA_DIR, so clear it to reach the
        # frozen branch.
        fake_exe_dir = tempfile.mkdtemp(prefix="chaoxing-frozen-")
        with patch.dict(os.environ, {"CHAOXING_DATA_DIR": ""}, clear=False):
            del os.environ["CHAOXING_DATA_DIR"]
            with patch.object(sys, "frozen", True, create=True):
                with patch.object(sys, "executable", os.path.join(fake_exe_dir, "chaoxing-gui.exe")):
                    self.assertEqual(paths.data_dir(), fake_exe_dir)

    def test_dev_falls_back_to_project_root(self):
        fake_exe_dir = tempfile.mkdtemp(prefix="chaoxing-dev-")
        with patch.dict(os.environ, {"CHAOXING_DATA_DIR": ""}, clear=False):
            del os.environ["CHAOXING_DATA_DIR"]
            with patch.object(sys, "frozen", False, create=True):
                # data_dir() for non-frozen returns dirname(dirname(paths.__file__)),
                # i.e. the repository root (parent of api/).
                expected = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))
                self.assertEqual(paths.data_dir(), expected)


if __name__ == "__main__":
    unittest.main()
