import unittest

from api.answer_check import match_answer, split_answers


class AnswerMatchingTests(unittest.TestCase):
    def test_choice_matching(self):
        options = 'A. C++\nB. TCP/IP\nC. 3.14\nD. Because'
        for answer, expected in [('A', 'A'), ('AC', 'AC'), ('A,C', 'AC'),
                                 ('C++', 'A'), ('TCP/IP', 'B'), ('Because', 'D'),
                                 ('3.14', 'C'), ('TCP', None), ('Because A', None)]:
            kind = 'multiple' if answer in ('AC', 'A,C') else 'single'
            with self.subTest(answer=answer):
                self.assertEqual(match_answer(answer, {'type': kind, 'options': options}).answer, expected)

    def test_single_option_with_enumeration_comma_is_not_split(self):
        answer = '坚持独立负责、不参与国际组织的活动'
        question = {'type': 'single', 'options': 'A. ' + answer + chr(10) + 'B. 其他'}
        self.assertEqual(match_answer(answer, question).answer, 'A')
        self.assertIsNone(match_answer('A、B', question).answer)
        self.assertEqual(match_answer('A、B', {**question, 'type': 'multiple'}).answer, 'AB')
        question['options'] = 'A. ' + answer + chr(10) + 'B. ' + answer
        self.assertIsNone(match_answer(answer, question).answer)

    def test_duplicate_or_partial_matches_are_not_answers(self):
        for options, answer in [('A. 相同\nB. 相同', '相同'), ('A. 苹果\nB. 香蕉', '苹果#西瓜')]:
            self.assertIsNone(match_answer(answer, {'type': 'multiple', 'options': options}).answer)

    def test_preserves_formula_and_code(self):
        for answer in ['a-b', 'a+b', '3.14', 'TCP/IP', 'int main() { return a | b; }']:
            self.assertEqual(split_answers(answer, 'shortanswer'), [answer])
        self.assertEqual(split_answers('["甲", "乙"]', 'multiple'), ['甲', '乙'])

    def test_completion_field_count(self):
        q = {'id': '12', 'type': 'completion', 'answerField': {'answer12_0': '', 'answer12_1': ''}}
        self.assertEqual(match_answer('甲#乙', q).fields, {'answer12_0': '甲', 'answer12_1': '乙'})
        self.assertIsNone(match_answer('甲', q).answer)




class MatchingBoundaryTests(unittest.TestCase):
    def test_multiple_separators_and_missing_labels(self):
        q = {'type': 'multiple', 'options': 'C. 苹果\nA. 香蕉'}
        for text in ['苹果|香蕉', '苹果,香蕉', '苹果###香蕉', '苹果===香蕉', '["苹果", "香蕉"]']:
            with self.subTest(text=text):
                self.assertEqual(match_answer(text, q).answer, 'CA')
        self.assertIsNone(match_answer('AB', q).answer)
        self.assertEqual(match_answer('["C", "A"]', q).answer, 'CA')
        self.assertEqual(split_answers('["甲"]', 'completion', 1), ['甲'])

    def test_formula_negation_and_duplicate_labels(self):
        for q, text in [({'type': 'single', 'options': 'A. a-b\nB. a+b'}, 'a*b'),
                        ({'type': 'single', 'options': 'A. 网络协议不可以进行这样的操作\nB. 其他选项'}, '网络协议可以进行这样的操作'),
                        ({'type': 'single', 'options': 'A. 苹果\nA. 香蕉'}, '苹果')]:
            with self.subTest(text=text):
                self.assertIsNone(match_answer(text, q).answer)

    def test_shortanswer_is_not_split_or_normalized(self):
        code = 'int main() {\n  return a | b;\n}'
        self.assertEqual(match_answer(code, {'type': 'shortanswer'}).answer, code)
        self.assertEqual(split_answers('a-b#c+d', 'completion', 2), ['a-b', 'c+d'])

if __name__ == "__main__":
    unittest.main()
