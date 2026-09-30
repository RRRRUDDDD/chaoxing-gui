"""Live markers use the attachment parser's rule, including falsey IDs."""

import unittest
from copy import deepcopy

from api.course_tools import CourseTools
from api.decode import decode_course_card
from tests.test_decode_jobs import card_page

# type, property overrides, expected live. `livestream` is a subset of `live`.
CASES = [
    ('video', {}, False),
    ('video', {'liveId': None, 'streamName': None, 'vdoid': None}, False),
    ('video', {'module': 'insertlive'}, False),
    ('live', {}, True),
    ('livestream', {}, True),
    ('LIVE', {}, True),
    ('video', {'type': 'LiveResource'}, True),
    ('video', {'resourceType': 'LIVE'}, True),
    ('video', {'liveId': 'id'}, True),
    ('video', {'streamName': ''}, True),
    ('video', {'vdoid': 0}, True),
    ('video', {'vdoid': False}, True),
]


def attachment(kind, prop):
    return {'type': kind, 'job': True, 'jobid': 'job', 'objectId': 'object', 'mid': 'mid',
            'property': {'objectid': 'object', **prop}}


class LiveCardTests(unittest.TestCase):
    def test_pending_and_passed_classification_share_the_truth_table(self):
        for kind, prop, expected in CASES:
            with self.subTest(kind=kind, prop=prop):
                card = attachment(kind, prop)
                pending, _ = decode_course_card(card_page([card]))
                self.assertEqual(pending[0]['type'] == 'live', expected)
                card.pop('job')
                card['isPassed'] = True
                _, info = decode_course_card(card_page([card]))
                self.assertEqual(info['passed_jobs'][0]['type'] == 'live', expected)

    def test_resource_scanner_excludes_exactly_the_live_cards(self):
        service = CourseTools(None)
        course = {'courseId': 'course', 'clazzId': 'class'}
        chapter = {'id': 'chapter', 'title': 'chapter'}
        for kind, prop, expected in CASES:
            with self.subTest(kind=kind, prop=prop):
                card = attachment(kind, prop)
                original = deepcopy(card)
                resource = service._resource(course, chapter, card, {}, 0, 0)
                self.assertEqual(resource is None, expected)
                self.assertEqual(card, original)
