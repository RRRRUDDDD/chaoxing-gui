"""Bounded image transport and OCS dev image-suggestion environment."""
import base64
import io
import re
from urllib.parse import urlsplit

import requests
from bs4 import NavigableString
from PIL import Image

from api.config import GlobalConst as gc

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_IMAGES = 20
MAX_PIXELS = 16_000_000
MIN_DIMENSION = 14
IMAGE_HOSTS = frozenset({'p.ananas.chaoxing.com'})


def checked_image_url(url):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) <= 32 or ord(c) == 127 or c == chr(92) for c in url):
        raise ValueError('invalid_image_url')
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == 'https' and parts.hostname in IMAGE_HOSTS
                 and parts.username is None and parts.password is None
                 and parts.port in (None, 443) and not parts.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('untrusted_image_url')
    return url


def download_image(url, session=None):
    checked_image_url(url)
    owned = session is None
    if owned:
        session = requests.Session()
        session.headers.update(gc.HEADERS)
    response = None
    try:
        response = session.get(url, headers={'Referer': 'https://mooc1.chaoxing.com/'},
                               timeout=8, stream=True, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('image_http_error')
        chunks, size = [], 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise ValueError('image_too_large')
            chunks.append(chunk)
        return b''.join(chunks)
    finally:
        if response is not None:
            response.close()
        if owned:
            session.close()


def png_data_url(content):
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError('image_too_large_or_empty')
    with Image.open(io.BytesIO(content)) as source:
        width, height = source.size
        if width < 1 or height < 1 or width * height > MAX_PIXELS:
            raise ValueError('image_pixel_limit')
        scale = max(1, MIN_DIMENSION / min(width, height))
        size = (round(width * scale), round(height * scale))
        if size[0] * size[1] > MAX_PIXELS:
            raise ValueError('image_pixel_limit')
        image = source.convert('RGBA')
        if scale > 1:
            resized = image.resize(size, Image.Resampling.LANCZOS)
            image.close()
            image = resized
        try:
            with io.BytesIO() as output:
                image.save(output, format='PNG')
                data = output.getvalue()
        finally:
            image.close()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('image_too_large')
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')


def validate_data_url(value):
    if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 100:
        raise ValueError('invalid_image_data')
    match = re.fullmatch(r'data:image/(?:png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        raise ValueError('invalid_image_data')
    try:
        content = base64.b64decode(match[1], validate=True)
    except ValueError:
        raise ValueError('invalid_image_data') from None
    return png_data_url(content)


def validate_images(values):
    if not isinstance(values, list) or len(values) > MAX_IMAGES:
        raise ValueError('invalid_image_list')
    result, total = [], 0
    for value in values:
        normalized = validate_data_url(value)
        total += len(normalized)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('image_total_limit')
        result.append(normalized)
    return result


def extract_image_text(element):
    """Keep image URLs in text before OCR; do not fetch during extraction."""
    if element is None:
        return '', []
    parts, urls = [], []
    for node in element.descendants:
        if isinstance(node, NavigableString):
            parts.append(str(node))
        elif node.name == 'img':
            url = node.get('src', '')
            if url:
                parts.append(' ' + url + ' ')
                urls.append(url)
        elif node.name == 'br':
            parts.append('\n')
    text = ''.join(parts).strip()
    label = re.match(r'([A-Z][.．、:：)）])', element.get('aria-label', '').strip())
    if urls and label and not text.startswith(label[1]):
        text = label[1] + ' ' + text
    return text, urls


def build_image_env(question, session=None):
    raw = question.get('_image_context') or {}
    title = raw.get('title', question.get('title', ''))
    options = raw.get('options', question.get('options', ''))
    if isinstance(options, list):
        options = '\n'.join(options)
    urls = list(dict.fromkeys(raw.get('urls', [])))
    images, uploaded, failures = [], [], []
    total = 0
    for url in urls[:MAX_IMAGES]:
        try:
            data = validate_data_url(url) if url.startswith('data:') else png_data_url(download_image(url, session))
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError('image_total_limit')
            images.append(data)
            uploaded.append(url)
        except (ValueError, OSError, requests.RequestException, Image.DecompressionBombError):
            failures.append('image_conversion_failed')
    if len(urls) > MAX_IMAGES:
        failures.append('image_count_limit')
    env = {'images': images, 'suggestion_title': '', 'suggestion_options': ''}
    if uploaded:
        mapping = {url: f' [图片{index}] ' for index, url in enumerate(uploaded, 1)}
        pattern = re.compile('|'.join(re.escape(url) for url in sorted(uploaded, key=len, reverse=True)))
        env['suggestion_title'] = pattern.sub(lambda m: mapping[m[0]], title)
        env['suggestion_options'] = pattern.sub(lambda m: mapping[m[0]], options)
    return env, uploaded, failures


def restore_image_answer(answer, urls):
    if not isinstance(answer, str):
        return answer
    return re.sub(r'\[图片(\d+)\]', lambda m: urls[int(m[1]) - 1]
                  if 0 < int(m[1]) <= len(urls) else m[0], answer)
