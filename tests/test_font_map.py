"""The encrypted-font table must load from the package, never from cwd."""

import builtins
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

import api.cxsecret_font as cxfont
from api.decode import decode_questions_info
from api.exceptions import FontDecodeError


QUESTION_PAGE = (
    "<html><style id='cxSecretStyle'>@font-face{src:url('data:application/font-ttf;"
    "charset=utf-8;base64,AAAA') format('truetype')}</style><form>"
    "<div class='singleQuesId' data='q1'><div class='TiMu' data='0'></div>"
    "<div class='Zy_TItle'>题目</div><ul><li aria-label='A 选项'></li></ul></div>"
    "</form></html>"
)


class FontMapTests(unittest.TestCase):
    def setUp(self):
        self.saved = cxfont._dao, cxfont._dao_error
        cxfont._dao, cxfont._dao_error = None, None
        self.addCleanup(self.restore)

    def restore(self):
        cxfont._dao, cxfont._dao_error = self.saved

    def test_table_loads_when_cwd_is_elsewhere(self):
        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                dao = cxfont.font_hash_dao()
            finally:
                os.chdir(previous)
        self.assertGreater(len(dao.hash_map), 0)
        self.assertIs(cxfont.font_hash_dao(), dao)

    def test_import_does_not_read_the_table(self):
        opened = []
        real_open = builtins.open

        def spy(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", spy):
            importlib.reload(cxfont)
        self.assertFalse([path for path in opened if "font_map_table" in path])
        self.assertIsNone(cxfont._dao)

    def test_missing_table_raises_instead_of_returning_glyphs(self):
        with patch.object(cxfont, "resource_path", return_value=os.path.join(tempfile.gettempdir(), "missing-font-map.json")):
            with self.assertRaises(FontDecodeError) as raised:
                cxfont.decrypt({"uni4E00": "hash"}, "\u4e00")
            self.assertIn("字体映射表缺失", str(raised.exception))
            # The failure is cached; the second call does not reread the disk.
            with patch.object(cxfont, "FontHashDAO", side_effect=AssertionError("reloaded")):
                with self.assertRaises(FontDecodeError):
                    cxfont.decrypt({}, "text")

    def test_undecodable_questions_are_marked_for_the_no_answer_path(self):
        cxfont._dao_error = "missing"
        with patch.object(cxfont, "font2map", return_value={"uni4E00": "hash"}):
            parsed = decode_questions_info(QUESTION_PAGE)
        question = parsed["questions"][0]
        self.assertTrue(question["undecodable"])
        self.assertEqual(question["title"], "题目")


if __name__ == "__main__":
    unittest.main()
