import base64
import io
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from PIL import Image

from api import question_images as images
from api.answer import CacheDAO
from api.decode import _process_question

URL = 'https://p.ananas.chaoxing.com/one.png'
SECOND = 'https://p.ananas.chaoxing.com/two.png'


def picture():
    with Image.new('RGB', (2, 3), 'white') as image, io.BytesIO() as out:
        image.save(out, format='PNG')
        return out.getvalue()


class QuestionImagesTests(unittest.TestCase):
    def test_defaults_and_ordered_mapping_with_partial_failure(self):
        q = {'_image_context': {'title': URL, 'options': SECOND + ' ' + URL,
                               'urls': [URL, SECOND, URL]}}
        with patch.object(images, 'download_image', side_effect=[ValueError('failed'), picture()]):
            env, urls, warnings = images.build_image_env(q)
        self.assertEqual(urls, [SECOND])
        self.assertEqual(env['suggestion_title'], URL)
        self.assertEqual(env['suggestion_options'], ' [图片1]  ' + URL)
        self.assertEqual(len(env['images']), 1)
        self.assertEqual(warnings, ['image_conversion_failed'])
        self.assertEqual(images.restore_image_answer('[图片1] [图片2]', urls), SECOND + ' [图片2]')

    def test_same_content_different_urls_keeps_both_positions(self):
        q = {'_image_context': {'title': URL + SECOND, 'options': '', 'urls': [URL, SECOND, URL]}}
        with patch.object(images, 'download_image', return_value=picture()) as download:
            env, urls, _ = images.build_image_env(q)
        self.assertEqual(download.call_count, 2)
        self.assertEqual(urls, [URL, SECOND])
        self.assertEqual(len(env['images']), 2)
        self.assertEqual(env['images'][0], env['images'][1])

    def test_transcodes_and_upscales_real_pixels(self):
        value = images.png_data_url(picture())
        with Image.open(io.BytesIO(base64.b64decode(value.split(',')[1]))) as result:
            self.assertEqual(result.format, 'PNG')
            self.assertEqual(result.size, (14, 21))
        with self.assertRaises((ValueError, OSError)):
            images.png_data_url(b'not-an-image')
        with patch.object(images, 'MAX_PIXELS', 1), self.assertRaises(ValueError):
            images.png_data_url(picture())
        with self.assertRaises(ValueError):
            images.validate_data_url('data:image/png;base64,%%%%')

    def test_url_and_download_limits(self):
        session = Mock()
        for url in ['http://p.ananas.chaoxing.com/a', 'https://p.ananas.chaoxing.com.evil.test/a',
                    'https://user:secret@p.ananas.chaoxing.com/a', 'https://127.0.0.1/a',
                    'https://p.ananas.chaoxing.com:444/a', 'file:///tmp/image']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                images.download_image(url, session)
        session.get.assert_not_called()
        response = Mock(status_code=302)
        session.get.return_value = response
        with self.assertRaises(ValueError):
            images.download_image(URL, session)
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        response.close.assert_called_once()
        response = Mock(status_code=200)
        response.iter_content.return_value = [b'12345', b'6789']
        session.get.return_value = response
        with patch.object(images, 'MAX_IMAGE_BYTES', 6), self.assertRaises(ValueError):
            images.download_image(URL, session)
        response.close.assert_called_once()
        session.close.assert_not_called()

    def test_extraction_preserves_images_before_ocr_and_cache_identity(self):
        html = f'<div data="1"><div class="TiMu" data="0"></div><div class="Zy_TItle">题<img src="{URL}"></div><ul><li>A. <img src="{SECOND}"></li><li>B. 无图</li></ul></div>'
        with patch('api.decode._ocr_image_to_text', return_value='formula'):
            q = _process_question(BeautifulSoup(html, 'lxml').div)
        self.assertIn('formula', q['title'])
        self.assertIn(SECOND, q['options'])
        self.assertEqual(q['_image_context']['urls'], [URL, SECOND])
        other = {**q, '_image_context': {**q['_image_context'], 'urls': [SECOND]}}
        self.assertNotEqual(CacheDAO.question_key(q), CacheDAO.question_key(other))

    def test_all_failed_and_count_limits(self):
        q = {'_image_context': {'title': URL, 'urls': [URL, SECOND]}}
        with patch.object(images, 'download_image', side_effect=ValueError()), patch.object(images, 'MAX_IMAGES', 1):
            env, urls, warnings = images.build_image_env(q)
        self.assertEqual(env, {'images': [], 'suggestion_title': '', 'suggestion_options': ''})
        self.assertEqual(urls, [])
        self.assertIn('image_count_limit', warnings)
        with patch.object(images, 'MAX_TOTAL_BYTES', 2), self.assertRaises(ValueError):
            images.validate_images([images.png_data_url(picture())])




class ImageParsingBoundaryTests(unittest.TestCase):
    def test_accessible_label_and_textarea_fields_survive(self):
        html = f'<div data="12"><div class="TiMu" data="2"></div><div class="Zy_TItle">Q</div><ul><li aria-label="A. 选择"><img src="{URL}"></li></ul><textarea name="answer12_0"></textarea><textarea name="answer12_1"></textarea></div>'
        with patch('api.decode._ocr_image_to_text', return_value=''):
            q = _process_question(BeautifulSoup(html, 'lxml').div)
        self.assertIn('A. ' + URL, q['options'])
        self.assertIn('answer12_0', q['answerField'])
        self.assertIn('answer12_1', q['answerField'])

if __name__ == "__main__":
    unittest.main()
