"""Execute both specs without installing PyInstaller in the test environment."""

from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class PackagingSpecTests(unittest.TestCase):
    def specs(self, version='1.6.1'):
        hooks = ModuleType('PyInstaller.utils.hooks')
        hooks.collect_submodules = lambda name: ['api.base', 'api.captcha']
        hooks.copy_metadata = lambda name: [('metadata', 'ddddocr-1.6.1.dist-info')]
        assets = SimpleNamespace(version=version, locate_file=lambda name: ROOT / 'wheel' / name)
        calls = []
        analysis = Mock(side_effect=lambda *args, **kwargs: (
            calls.append(kwargs) or SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])))
        context = dict(SPECPATH=str(ROOT), Analysis=analysis, PYZ=Mock(), EXE=Mock(), COLLECT=Mock())
        with patch.dict(sys.modules, {'PyInstaller.utils.hooks': hooks}), \
                patch('importlib.metadata.distribution', return_value=assets), patch.object(sys, 'path', sys.path[:]):
            sys.modules.pop('spec_common', None)
            try:
                for name in ('chaoxing.spec', 'chaoxing-backend.spec'):
                    runpy.run_path(str(ROOT / name), init_globals=context)
            finally:
                sys.modules.pop('spec_common', None)
        return calls

    def test_shared_resources_are_identical_and_tray_settings_do_not_leak(self):
        gui, backend = self.specs()
        self.assertEqual(gui['datas'], backend['datas'])
        self.assertIn('pystray', gui['hiddenimports'])
        self.assertNotIn('pystray', backend['hiddenimports'])
        self.assertNotIn('pystray', gui['excludes'])
        self.assertIn('pystray', backend['excludes'])
        for spec in (gui, backend):
            icons = [entry for entry in spec['datas'] if Path(entry[0]).name == 'fav.jpg']
            self.assertEqual(icons, [(str(ROOT / 'fav.jpg'), '.')])
            models = [Path(source).name for source, target in spec['datas'] if target == 'ddddocr']
            self.assertEqual(models, ['common_old.onnx', 'charsets.py'])
            self.assertIn('openai', spec['excludes'])
            self.assertIn('pydantic_core', spec['excludes'])

    def test_unverified_captcha_version_fails_packaging(self):
        with self.assertRaisesRegex(SystemExit, 'ddddocr==1.6.1'):
            self.specs(version='other')
