"""Validate portable entry wiring without running destructive build scripts."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PortableScriptTests(unittest.TestCase):
    def test_only_one_portable_build_entry_remains(self):
        self.assertFalse((ROOT / 'build_portable.bat').exists())
        quick = (ROOT / 'quick_build.bat').read_text(encoding='utf-8')
        self.assertIn('clean_and_build_portable.bat', quick)
        self.assertNotIn('运行 build_portable.bat', quick)
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('(DEVELOPMENT.md)', readme)
        guide = (ROOT / 'DEVELOPMENT.md').read_text(encoding='utf-8')
        self.assertIn('clean_and_build_portable.bat', guide)
        self.assertIn('不含个人数据的独立源码副本', guide)

    def test_flask_cors_comes_from_requirements_not_an_extra_install(self):
        for filename in ('clean_and_build_portable.bat', 'start.bat'):
            script = (ROOT / filename).read_text(encoding='utf-8')
            for line in script.splitlines():
                if 'pip install' in line:
                    self.assertNotIn('flask-cors', line)
            self.assertIn('requirements.txt', script)
