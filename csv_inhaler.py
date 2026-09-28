#!/usr/bin/env python3
"""csv-inhaler: clean a dirty CSV file with a decision model.

Each line is parsed with Python's csv module in strict mode and, if it parses,
written out as is. Otherwise a decision model (Ollaya, https://ollaya.dev, or
TypeSafe's Jev) is asked yes/no questions: whether each following line break
ends the record, then, in one batched request, whether each comma in the record
separates two fields. Quotes are never asked about: a field that starts and
ends with a quote is unquoted. Only framing is repaired; values are never
changed. A line whose reading does not have the right number of fields is
reported and skipped.
"""
import argparse, csv, json, os, sys, time, urllib.error, urllib.request

DEFAULT_HOST = "http://127.0.0.1:11435"


def parse(text, delim, quote):
    """Fields of `text` if it is one strict RFC 4180 record (Python's csv module in strict mode), else None."""
    try:
        return next(csv.reader([text], delimiter=delim, quotechar=quote, strict=True))
    except csv.Error:
        return None


def question(kind, char, before, after):
    """(instructions, yes, no) for one decision, with the text around it as context."""
    ctx = "between %r and %r" % (before, after)
    if kind == "delimiter":
        return ("Is the %r %s a separator between two fields?" % (char, ctx),
                "a field separator", "part of the text of one value")
    return ("Is the line break %s the end of a record, rather than a line break inside a text value?" % ctx,
            "the end of a record: the next line is a new record", "a line break inside a value: one record")


class Model:
    """Batched `noul` questions to a TypeSafe-compatible /v1/systemone endpoint (Ollaya or api.typesafe.ai)."""

    def __init__(self, host, name, timeout=120):
        host = host or os.environ.get("OLLAYA_HOST") or DEFAULT_HOST
        self.host = (host if "://" in host else "http://" + host).rstrip("/")
        self.name, self.timeout, self.requests = name, timeout, 0

    def ask(self, state, questions):
        """questions: {id: (instructions, yes, no)} -> {id: P(yes)}."""
        body = {"model": self.name, "state": state, "questions": {
            qid: {"type": "noul", "instructions": q, "criteria": {"true": yes, "false": no}}
            for qid, (q, yes, no) in questions.items()}}
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("OLLAYA_API_KEY")
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(self.host + "/v1/systemone", json.dumps(body).encode(), headers)
        self.requests += 1
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    answers = json.load(r)["answers"]
                return {qid: answers[qid]["noul"] for qid in questions}
            except urllib.error.HTTPError as e:
                if e.code in (429, 529) and attempt < 5:
                    time.sleep(2 ** attempt); continue
                raise SystemExit("csv-inhaler: %s answered HTTP %d: %s" % (self.host, e.code, e.read().decode(errors="replace")[:500]))
            except urllib.error.URLError as e:
                raise SystemExit("csv-inhaler: cannot reach %s (%s)" % (self.host, e.reason))


class Inhaler:
    def __init__(self, model, names, delim=",", quote='"', examples=3, max_lines=50, log=None):
        self.model, self.names, self.delim, self.quote = model, names, delim, quote
        self.examples, self.want_examples, self.max_lines, self.log = [], examples, max_lines, log
        self.skipped = 0

    def run(self, lines):
        """lines: iterator of physical lines without the newline. Yields records in file order."""
        line = next(lines, None)
        while line is not None:
            fields = parse(line, self.delim, self.quote)
            if fields is not None and len(fields) == len(self.names):
                if len(self.examples) < self.want_examples:
                    self.examples.append(line)
                yield fields
                line = next(lines, None)
                continue
            # a broken record: pull in following lines while the model says the line break is inside a value
            decisions, text = [], line
            line = next(lines, None)
            while line is not None and text.count("\n") < self.max_lines:
                p = self.model.ask(self.state(text + "\n" + line),
                                   {"n": question("newline", "\n", text[-14:], line[:14])})["n"]
                decisions.append({"kind": "newline", "position": len(text), "probability": p, "syntax": p >= 0.5})
                if p >= 0.5:
                    break
                text, line = text + "\n" + line, next(lines, None)
            record = self.decode(text, decisions)
            if record is not None:
                yield record

    def state(self, text):
        s = "CSV file with %d columns: %s. Delimiter %r, quote %s.\n" % (
            len(self.names), ", ".join(self.names), self.delim, self.quote)
        s += "".join("Valid row from the same file: %r\n" % e for e in self.examples)
        return s + "Malformed text: %r\n" % text

    def decode(self, text, decisions):
        """Ask about every delimiter in one request, split accordingly, unquote fields that are quoted."""
        d, q = self.delim, self.quote
        positions = [i for i in range(len(text)) if text.startswith(d, i)]
        answers = self.model.ask(self.state(text), {
            "d%d" % k: question("delimiter", d, text[max(0, i - 14):i], text[i + len(d):i + len(d) + 14])
            for k, i in enumerate(positions)}) if positions else {}
        separators = set()
        for k, i in enumerate(positions):
            p = answers["d%d" % k]
            decisions.append({"kind": "delimiter", "position": i, "probability": p, "syntax": p >= 0.5})
            if p >= 0.5:
                separators.add(i)
        fields, cur, i = [], "", 0
        while i < len(text):
            if i in separators:
                fields.append(cur); cur = ""; i += len(d)
            else:
                cur += text[i]; i += 1
        fields.append(cur)
        fields = [f[1:-1].replace(q + q, q) if len(f) >= 2 and f[0] == f[-1] == q else f for f in fields]
        ok = len(fields) == len(self.names)
        if self.log:
            self.log.write(json.dumps({"text": text, "fields": fields, "ok": ok, "decisions": decisions}, ensure_ascii=False) + "\n")
        if not ok:
            self.skipped += 1
            sys.stderr.write("csv-inhaler: skipped %r: the model read %d fields instead of %d: %r\n" % (
                text, len(fields), len(self.names), fields))
            return None
        return fields


def main(argv=None):
    ap = argparse.ArgumentParser(prog="csv-inhaler", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", default="-", help="CSV file, or - for stdin (default)")
    ap.add_argument("--model", default="laya:en", help="model name (default %(default)s; jev-latest on TypeSafe)")
    ap.add_argument("--host", default=None, help="Ollaya server (default $OLLAYA_HOST or %s), or https://api.typesafe.ai "
                    "with TYPESAFE_API_KEY set" % DEFAULT_HOST)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("-d", "--delimiter", default=None, help="default: the most frequent of , ; tab | in the header")
    ap.add_argument("-q", "--quote", default='"')
    ap.add_argument("--names", default=None, help="comma-separated column names for a file without a header")
    ap.add_argument("--examples", type=int, default=3, help="clean rows shown to the model as context")
    ap.add_argument("--max-lines", type=int, default=50, help="most physical lines joined into one record")
    ap.add_argument("--log", default=None, help="write one JSON line per repaired record (with every decision) here")
    a = ap.parse_args(argv)

    inp = sys.stdin if a.input == "-" else open(a.input, encoding="utf-8", errors="replace", newline="")
    lines = (l.rstrip("\r\n") for l in inp)
    if a.names:
        names, delim = a.names.split(","), a.delimiter or ","
    else:
        header = next(lines, "")
        delim = a.delimiter or max([",", ";", "\t", "|"], key=header.count)
        names = parse(header, delim, a.quote) or header.split(delim)
    out = csv.writer(sys.stdout, delimiter=delim, quotechar=a.quote, lineterminator="\n")
    if not a.names:
        out.writerow(names)
    model = Model(a.host, a.model, a.timeout)
    log = open(a.log, "w", encoding="utf-8") if a.log else None
    inhaler = Inhaler(model, names, delim, a.quote, a.examples, a.max_lines, log)
    for record in inhaler.run(lines):
        out.writerow(record); sys.stdout.flush()
    sys.stderr.write("csv-inhaler: %d model requests, %d lines skipped\n" % (model.requests, inhaler.skipped))
    return 1 if inhaler.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
