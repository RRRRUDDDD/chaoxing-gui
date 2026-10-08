"""config.ini/CLI 数值配置的解析契约。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from api.exceptions import InputFormatError

import main


class BuildConfigFromArgsTest(unittest.TestCase):
    def _args(self, *extra):
        argv = ["main.py", "-u", "u", "-p", "p", *extra]
        with mock.patch.object(sys, "argv", argv):
            return main.parse_args()

    def test_explicit_zero_retry_interval_is_not_swallowed(self):
        common_config, _, _ = main.build_config_from_args(self._args("--retry-interval", "0"))
        self.assertEqual(common_config["retry_interval"], 0)

    def test_default_retry_interval_stays_one_second(self):
        common_config, _, _ = main.build_config_from_args(self._args())
        self.assertEqual(common_config["retry_interval"], 1.0)


class LoadConfigFromFileTest(unittest.TestCase):
    def _config_path(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False, encoding="utf8")
        with handle:
            handle.write(text)
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_bad_common_value_raises_located_error(self):
        path = self._config_path("[common]\nspeed = abc\n")
        with self.assertRaises(InputFormatError) as ctx:
            main.load_config_from_file(path)
        self.assertIn("[common] speed", str(ctx.exception))

    def test_bad_jobs_value_raises_located_error(self):
        path = self._config_path("[common]\njobs = 2.5\n")
        with self.assertRaises(InputFormatError) as ctx:
            main.load_config_from_file(path)
        self.assertIn("[common] jobs", str(ctx.exception))

    def test_bad_tiku_value_raises_located_error(self):
        path = self._config_path("[tiku]\ndelay = oops\n")
        with self.assertRaises(InputFormatError) as ctx:
            main.load_config_from_file(path)
        self.assertIn("[tiku] delay", str(ctx.exception))

    def test_good_values_convert_and_retry_interval_defaults(self):
        path = self._config_path("[common]\nspeed = 1.5\njobs = 2\n")
        common_config, tiku_config, _ = main.load_config_from_file(path)
        self.assertEqual(common_config["speed"], 1.5)
        self.assertEqual(common_config["jobs"], 2)
        self.assertEqual(common_config["retry_interval"], 1.0)
        self.assertEqual(tiku_config, {})

    def test_percent_in_values_is_not_interpolated(self):
        path = self._config_path(
            "[common]\npassword = abc%123def\n"
            "[tiku]\nconfig = https://example.com/sub%2Fkey\n"
        )
        common_config, tiku_config, _ = main.load_config_from_file(path)
        self.assertEqual(common_config["password"], "abc%123def")
        self.assertEqual(tiku_config["config"], "https://example.com/sub%2Fkey")


if __name__ == "__main__":
    unittest.main()
