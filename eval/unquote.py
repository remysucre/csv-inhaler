#!/usr/bin/env python3
"""Realistic in-scope pollution: take a valid CSV, pick rows whose quoted cell contains a delimiter, a line break
or a quote, and remove that cell's quoting (un-doubling any escaped quotes). Then check whether csv-inhaler
restores the original records.

    python3 eval/unquote.py file.csv --rows 10 --model winnow:e4b [--limit LINES]
"""
import argparse, csv, io, os, random, sys, time
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import csv_inhaler as ci  # noqa: E402


def unquote_cell(line, delim, index):
    """Rewrite record `line` with cell `index` unquoted. Returns None if that cell is not quoted."""
    out, i, n = [], 0, 0
    while i <= len(line):
        if line.startswith('"', i):  # quoted cell: find its end
            j = i + 1
            while j < len(line):
                if line.startswith('""', j):
                    j += 2
                elif line[j] == '"':
                    break
                else:
                    j += 1
            cell = line[i:j + 1]
            if n == index:
                cell = cell[1:-1].replace('""', '"')
            i = j + 1
        else:
            j = line.find(delim, i)
            j = len(line) if j < 0 else j
            cell = line[i:j]
            if n == index:
                return None
            i = j
        out.append(cell)
        n += 1
        i += len(delim)
    return delim.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--rows", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="use only the first LINES physical lines")
    ap.add_argument("--model", default="winnow:e4b")
    ap.add_argument("--examples", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args()

    text = open(a.file, encoding="utf-8", errors="replace", newline="").read()
    delim = max([",", ";", "\t", "|"], key=text.split("\n", 1)[0].count)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    names, rows = rows[0], rows[1:]
    if a.limit:
        rows = rows[:a.limit]
    # rebuild each record as one strictly quoted line so cells can be located, keeping line breaks inside cells
    lines = [",".join('"%s"' % c.replace('"', '""') if any(x in c for x in (delim, "\n", '"')) else c for c in r) for r in rows]
    candidates = [(i, j) for i, r in enumerate(rows) for j, c in enumerate(r) if any(x in c for x in (delim, "\n", '"'))]
    chosen = random.Random(a.seed).sample(candidates, min(a.rows, len(candidates)))
    for i, j in chosen:
        lines[i] = unquote_cell(lines[i], delim, j)
    polluted = [l for line in lines for l in line.split("\n")]  # physical lines, as a file would have them

    model = ci.Model(None, a.model, a.timeout)
    log = io.StringIO()
    inh = ci.Inhaler(model, names, delim, '"', a.examples, log=log)
    err, t0 = io.StringIO(), time.time()
    with redirect_stderr(err):
        got = list(inh.run(iter(polluted)))
    per_decision = score_decisions(log.getvalue(), lines, delim)
    kinds = {"delimiter": 0, "newline": 0, "quote": 0}
    for i, j in chosen:
        c = rows[i][j]
        kinds["newline" if "\n" in c else "quote" if '"' in c else "delimiter"] += 1
    polluted_ok = sum(1 for i, j in chosen if rows[i] in got)
    print("%s: %d records, %d unquoted cells (%s)" % (os.path.basename(a.file), len(rows), len(chosen),
          ", ".join("%d with %s" % (v, k) for k, v in kinds.items() if v)))
    print("  restored exactly: %d of %d polluted records; %d of %d records overall; %d skipped; %d requests; %.0f s"
          % (polluted_ok, len(chosen), sum(1 for r in rows if r in got), len(rows), inh.skipped, model.requests, time.time() - t0))
    for kind, (right, total) in sorted(per_decision.items()):
        print("  %-9s decisions: %d of %d right (%.0f%%)" % (kind, right, total, 100.0 * right / max(1, total)))
    for l in err.getvalue().splitlines():
        print("  " + l[:240])


def truth_positions(line, delim):
    """Which delimiter/quote positions in a strictly quoted record line are syntax."""
    syntax, i, state = set(), 0, "start"
    while i < len(line):
        if state == "quoted":
            if line.startswith('""', i):
                i += 2
            elif line[i] == '"':
                syntax.add(i); state = "closed"; i += 1
            else:
                i += 1
        elif line.startswith(delim, i):
            syntax.add(i); state = "start"; i += len(delim)
        elif state == "start" and line[i] == '"':
            syntax.add(i); state = "quoted"; i += 1
        else:
            state = "plain"; i += 1
    return syntax


def score_decisions(log_text, lines, delim):
    """Per kind: (right, total) over logged decisions whose text is exactly one record of the polluted file."""
    import json
    by_text = {l: truth_positions(l, delim) for l in lines}
    score = {}
    for entry in log_text.splitlines():
        e = json.loads(entry)
        truth = by_text.get(e["text"])
        if truth is None:
            continue  # the model joined or split records wrongly; positions no longer line up
        for d in e["decisions"]:
            if d["kind"] == "newline":
                want = d["position"] >= len(e["text"])  # the break after the record ends it; inner ones are text
            else:
                want = d["position"] in truth
            r, t = score.get(d["kind"], (0, 0))
            score[d["kind"]] = (r + (d["syntax"] == want), t + 1)
    return score


if __name__ == "__main__":
    main()
