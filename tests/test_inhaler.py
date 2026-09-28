"""Offline tests: a fake model answers from an oracle over the question text. Run: python3 -m unittest discover -s tests"""
import io, json, os, sys, unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import csv_inhaler as ci  # noqa: E402

NAMES = ["id", "name", "note", "amount"]


class FakeModel:
    def __init__(self, oracle):
        self.oracle, self.requests, self.questions = oracle, 0, []

    def ask(self, state, questions):
        self.requests += 1
        self.questions += [q for q, _, _ in questions.values()]
        return {qid: self.oracle(q) for qid, (q, _, _) in questions.items()}


def syntax_unless(*text_snippets):
    """Every comma separates and every line break ends a record, except where the question mentions a snippet."""
    return lambda q: 0.1 if any(s in q for s in text_snippets) else 0.9


def run(lines, oracle, **kw):
    model = FakeModel(oracle)
    return list(ci.Inhaler(model, NAMES, **kw).run(iter(lines))), model


class ParseTests(unittest.TestCase):
    def test_strict(self):
        self.assertEqual(ci.parse('1,"a, b","say ""hi""",', ",", '"'), ["1", "a, b", 'say "hi"', ""])
        for bad in ['1,"a"b,2', '1,"open', "1,two\nlines"]:
            self.assertIsNone(ci.parse(bad, ",", '"'), bad)


class RepairTests(unittest.TestCase):
    def test_clean_lines_never_ask(self):
        records, model = run(["1,a,b,10", '2,"c, d",e,20'], syntax_unless())
        self.assertEqual(records, [["1", "a", "b", "10"], ["2", "c, d", "e", "20"]])
        self.assertEqual(model.requests, 0)

    def test_stray_comma_one_batched_request(self):
        records, model = run(["1,a,b,10", "2,c,x, y,20"], syntax_unless("x' and ' y"))
        self.assertEqual(records, [["1", "a", "b", "10"], ["2", "c", "x, y", "20"]])
        self.assertEqual(model.requests, 1)  # last line: no join question possible, one request for all commas
        self.assertEqual(sum("separator" in q for q in model.questions), 4)

    def test_quotes_by_rule_not_by_question(self):
        # a field that starts and ends with a quote is unquoted and un-doubled; no quote question is ever asked
        records, model = run(['2,"bob, jr","say ""hi""",20,x'], syntax_unless("bob' and ' jr"))
        self.assertEqual(records, [])  # 5 fields: skipped (the trailing x is deliberate)
        records, model = run(['2,"bob, jr","say ""hi""",20'], syntax_unless("bob' and ' jr"))
        self.assertEqual(records, [["2", "bob, jr", 'say "hi"', "20"]])
        self.assertFalse(any("quote" in q for q in model.questions))

    def test_literal_quotes_kept(self):
        records, _ = run(['4,dave,5" pipe,x,40'], syntax_unless("pipe' and 'x"))
        self.assertEqual(records, [["4", "dave", '5" pipe,x', "40"]])

    def test_newline_inside_value_joins_lines(self):
        records, model = run(["14,noah,line one", "line two,140", "15,pat,ok,150"], syntax_unless("one' and 'line two"))
        self.assertEqual(records, [["14", "noah", "line one\nline two", "140"], ["15", "pat", "ok", "150"]])
        self.assertEqual(model.requests, 3)  # join yes, join no, one batch of commas

    def test_wrong_field_count_is_reported_and_skipped(self):
        err = io.StringIO()
        with redirect_stderr(err):
            records, _ = run(["5,erin,50", "4,dave,too,many,fields,40", "7,g,h,70"], syntax_unless())
        self.assertEqual(records, [["7", "g", "h", "70"]])
        self.assertEqual(err.getvalue().count("skipped"), 2)

    def test_decisions_are_logged(self):
        log = io.StringIO()
        run(["1,a,x, y,10"], syntax_unless("x' and ' y"), log=log)
        entry = json.loads(log.getvalue())
        self.assertEqual(entry["fields"], ["1", "a", "x, y", "10"])
        self.assertEqual([d["syntax"] for d in entry["decisions"]], [True, True, False, True])


if __name__ == "__main__":
    unittest.main()
