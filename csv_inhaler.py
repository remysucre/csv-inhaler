#!/usr/bin/env python3
"""csv-inhaler: clean a dirty CSV file with a local decision model.

Each line is parsed with Python's csv module in strict mode and, if it parses,
written out as is.
Otherwise the model (Ollaya, https://ollaya.dev) is asked, one yes/no question
at a time: first whether the following lines belong to the same record, then,
walking the text character by character, whether each delimiter and quote is
CSV syntax or data.
"""
import argparse, csv, json, os, sys, urllib.request

DEFAULT_HOST = "http://127.0.0.1:11435"


def parse(text, delim, quote):
    """Fields of `text` if it is one strict RFC 4180 record (Python's csv module in strict mode), else None."""
    try:
        return next(csv.reader([text], delimiter=delim, quotechar=quote, strict=True))
    except csv.Error:
        return None


class Model:
    """One yes/no (`noul`) question per request to Ollaya's /v1/decisions endpoint."""

    def __init__(self, host, name, timeout=120):
        host = host or os.environ.get("OLLAYA_HOST") or DEFAULT_HOST
        self.host = (host if "://" in host else "http://" + host).rstrip("/")
        self.name, self.timeout, self.requests = name, timeout, 0

    def decide(self, state, question, yes, no):
        body = {"model": self.name, "state": state,
                "questions": {"q": {"type": "noul", "instructions": question, "criteria": {"true": yes, "false": no}}}}
        headers = {"Content-Type": "application/json"}
        if os.environ.get("OLLAYA_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["OLLAYA_API_KEY"]
        req = urllib.request.Request(self.host + "/v1/decisions", json.dumps(body).encode(), headers)
        self.requests += 1
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)["answers"]["q"]["noul"]


class Inhaler:
    def __init__(self, model, names, delim=",", quote='"', examples=3, max_lines=50, log=None):
        self.model, self.names, self.delim, self.quote = model, names, delim, quote
        self.examples, self.want_examples, self.max_lines, self.log = [], examples, max_lines, log
        self.decisions, self.skipped = [], 0

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
            self.decisions, text = [], line
            line = next(lines, None)
            while line is not None and text.count("\n") < self.max_lines and not self.ask(
                    text + "\n" + line, len(text), "newline",
                    "Is the line break between %r and %r the end of a record, rather than a line break "
                    "inside a text value?" % (text[-30:], line[:30]),
                    "the end of a record: the next line is a new record", "a line break inside a value: one record"):
                text, line = text + "\n" + line, next(lines, None)
            record = self.decode(text)
            if record is not None:
                yield record

    def ask(self, text, pos, kind, question, yes, no, fields=None, cur=""):
        state = "CSV file with %d columns: %s. Delimiter %r, quote %s.\n" % (
            len(self.names), ", ".join(self.names), self.delim, self.quote)
        state += "".join("Valid row from the same file: %r\n" % e for e in self.examples)
        state += "Malformed text: %r\n" % text
        if fields is not None:
            state += "Fields read so far: %s\nCurrent field so far: %r\n" % (
                ", ".join("%s=%r" % nf for nf in zip(self.names, fields)), cur)
        p = self.model.decide(state, question, yes, no)
        self.decisions.append({"kind": kind, "position": pos, "probability": p, "syntax": p >= 0.5})
        return p >= 0.5

    def decode(self, text):
        """Walk `text` character by character; every delimiter outside quotes and every quote is a question."""
        d, q = self.delim, self.quote
        fields, cur, in_quotes, i = [], "", False, 0
        while i < len(text):
            ctx = "between %r and %r" % (text[max(0, i - 14):i], text[i + 1:i + 15])
            if text.startswith(d, i) and not in_quotes:
                if self.ask(text, i, "delimiter", "Is the %r %s a separator between two fields?" % (d, ctx),
                            "a field separator", "part of the text of one value", fields, cur):
                    fields, cur = fields + [cur], ""
                else:
                    cur += d
                i += len(d)
            elif text.startswith(q, i):
                if self.ask(text, i, "quote", "Is the quote %s %s CSV syntax, opening or closing a quoted value, "
                            "rather than a literal character of the text?" % (q, ctx),
                            "CSV syntax: a quoting mark", "a literal quote character inside the text", fields, cur):
                    in_quotes = not in_quotes
                else:
                    cur += q
                i += 1
            else:
                cur += text[i]; i += 1
        fields.append(cur)
        ok = len(fields) == len(self.names)
        if self.log:
            self.log.write(json.dumps({"text": text, "fields": fields, "ok": ok, "decisions": self.decisions},
                                      ensure_ascii=False) + "\n")
        if not ok:
            self.skipped += 1
            sys.stderr.write("csv-inhaler: skipped %r: the model read %d fields instead of %d: %r\n" % (
                text, len(fields), len(self.names), fields))
            return None
        return fields


def main(argv=None):
    ap = argparse.ArgumentParser(prog="csv-inhaler", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", default="-", help="CSV file, or - for stdin (default)")
    ap.add_argument("--model", default="laya:en", help="Ollaya model (default %(default)s; winnow:e4b decides better)")
    ap.add_argument("--host", default=None, help="Ollaya server (default $OLLAYA_HOST or %s)" % DEFAULT_HOST)
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
