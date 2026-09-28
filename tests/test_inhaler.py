"""Offline tests: a fake model answers from an oracle over the question text. Run: python3 -m unittest discover -s tests"""
import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import csv_inhaler as ci  # noqa: E402

NAMES = ["id", "name", "note", "amount"]


class FakeModel:
    def __init__(self, oracle):
        self.oracle, self.questions = oracle, []

    def decide(self, state, question, yes, no):
        self.questions.append(question)
        return self.oracle(question)


def syntax_unless(*text_snippets):
    """Everything is CSV syntax except decisions whose question mentions one of the snippets."""
    return lambda q: 0.1 if any(s in q for s in text_snippets) else 0.9


def run(lines, oracle, **kw):
    model = FakeModel(oracle)
    return list(ci.Inhaler(model, NAMES, **kw).run(iter(lines))), model


class ParseTests(unittest.TestCase):
    def test_strict(self):
        self.assertEqual(ci.parse('1,"a, b","say ""hi""",', ",", '"'), ["1", "a, b", 'say "hi"', ""])
        self.assertEqual(ci.parse("a;b", ";", '"'), ["a", "b"])
        for bad in ['1,"a"b,2', '1,"open', "1,two\nlines"]:
            self.assertIsNone(ci.parse(bad, ",", '"'), bad)
        self.assertEqual(ci.parse('1,5" pipe,2', ",", '"'), ["1", '5" pipe', "2"])  # a quote mid-value is text


class RepairTests(unittest.TestCase):
    def test_clean_lines_never_ask(self):
        records, model = run(["1,a,b,10", '2,"c, d",e,20'], syntax_unless())
        self.assertEqual(records, [["1", "a", "b", "10"], ["2", "c, d", "e", "20"]])
        self.assertEqual(model.questions, [])

    def test_stray_comma(self):
        records, model = run(["1,a,b,10", "2,c,x, y,20", "3,e,f,30"], syntax_unless("x' and ' y"))
        self.assertEqual(records[1], ["2", "c", "x, y", "20"])
        self.assertEqual([r[0] for r in records], ["1", "2", "3"])
        # the clean line before it was offered as context, and the next line was asked about as a join
        self.assertTrue(any("line break" in q for q in model.questions))

    def test_literal_quote(self):
        records, model = run(['4,dave,5" pipe,40'], syntax_unless("quote"))
        self.assertEqual((records, model.questions), ([["4", "dave", '5" pipe', "40"]], []))
        records, _ = run(['4,dave,5" pipe,x,40'], syntax_unless("quote", "pipe' and 'x"))
        self.assertEqual(records, [["4", "dave", '5" pipe,x', "40"]])

    def test_quoting_marks_make_delimiters_literal_without_asking(self):
        records, model = run(['2,"bob, jr",x,20'], syntax_unless())
        self.assertEqual(records, [["2", "bob, jr", "x", "20"]])
        self.assertFalse(any("'2,\"bob'" in q for q in model.questions))  # the comma inside quotes: no question

    def test_escaped_quote_is_syntax_then_text(self):
        # the model reads the RFC escape as: first quote closes (syntax), second is text, third reopens
        oracle = lambda q: 0.1 if "say\"' and '\" hi" in q else 0.9
        records, _ = run(['1,"say ""hi""",2,3'], oracle)
        self.assertEqual(records[0][1], 'say "hi"')

    def test_newline_inside_value_joins_lines(self):
        records, _ = run(["14,noah,line one", "line two,140", "15,pat,ok,150"], syntax_unless("one' and 'line two"))
        self.assertEqual(records, [["14", "noah", "line one\nline two", "140"], ["15", "pat", "ok", "150"]])

    def test_wrong_field_count_is_reported_and_skipped(self):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            records, _ = run(["5,erin,50", "4,dave,too,many,fields,40", "7,g,h,70"], syntax_unless())
        self.assertEqual(records, [["7", "g", "h", "70"]])
        self.assertEqual(err.getvalue().count("skipped"), 2)
        self.assertIn("6 fields instead of 4", err.getvalue())

    def test_decisions_are_logged(self):
        import io, json
        log = io.StringIO()
        run(["1,a,x, y,10"], syntax_unless("x' and ' y"), log=log)
        entry = json.loads(log.getvalue())
        self.assertEqual(entry["fields"], ["1", "a", "x, y", "10"])
        self.assertEqual([d["syntax"] for d in entry["decisions"]], [True, True, False, True])


if __name__ == "__main__":
    unittest.main()
