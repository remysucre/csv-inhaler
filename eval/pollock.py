#!/usr/bin/env python3
"""Evaluate csv-inhaler on Pollock (https://github.com/HPI-Information-Systems/Pollock) files that are in scope:
row_extra_quote* (an extra unescaped quote in one cell) and file_escape_char_* (backslash / no escaping).

    python3 eval/pollock.py /path/to/Pollock --model winnow:e4b --sample 40

For each polluted file the tool's records are compared with the clean file's records (as parsed by Python's
csv module). Reports records recovered, lines skipped and model requests. `--files` evaluates given paths
(polluted,clean pairs are looked up by name under polluted_files/ or survey_sample/).
"""
import argparse, csv, io, os, random, sys, time
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import csv_inhaler as ci  # noqa: E402


def records_of(path, delim=None):
    lines = [l.rstrip("\r\n") for l in open(path, encoding="utf-8", errors="replace", newline="")]
    header = lines[0] if lines else ""
    delim = delim or max([",", ";", "\t", "|"], key=header.count)
    names = ci.parse(header, delim, '"') or header.split(delim)
    return lines, names, delim


def clean_records(path, delim):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.reader(f, delimiter=delim))
    return rows[1:]  # drop the header


def evaluate(polluted, clean, model, examples, max_lines):
    lines, names, delim = records_of(polluted)
    truth = clean_records(clean, delim)
    err = io.StringIO()
    inhaler = ci.Inhaler(model, names, delim, '"', examples, max_lines)
    before = model.requests
    t0 = time.time()
    with redirect_stderr(err):
        got = list(inhaler.run(iter(lines[1:])))
    remaining = list(map(tuple, truth))
    recovered = 0
    for r in got:
        if tuple(r) in remaining:
            remaining.remove(tuple(r)); recovered += 1
    return {"records": len(truth), "recovered": recovered, "output": len(got), "skipped": inhaler.skipped,
            "requests": model.requests - before, "seconds": time.time() - t0, "stderr": err.getvalue()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pollock", help="path to a Pollock checkout")
    ap.add_argument("--model", default="laya:en")
    ap.add_argument("--host", default=None)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--sample", type=int, default=20, help="how many row_extra_quote files to draw")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--examples", type=int, default=3)
    ap.add_argument("--max-lines", type=int, default=50)
    ap.add_argument("--files", nargs="*", help="explicit polluted file names instead of a sample")
    ap.add_argument("-v", action="store_true", help="print the tool's stderr for each file")
    a = ap.parse_args()

    csv_dir, clean_dir = os.path.join(a.pollock, "polluted_files", "csv"), os.path.join(a.pollock, "polluted_files", "clean")
    if a.files:
        names = a.files
    else:
        quotes = sorted(f for f in os.listdir(csv_dir) if f.startswith("row_extra_quote"))
        names = random.Random(a.seed).sample(quotes, a.sample) + ["file_escape_char_0x5C.csv", "file_escape_char_0x00.csv"]
    model = ci.Model(a.host, a.model, a.timeout)
    total = {"records": 0, "recovered": 0, "skipped": 0, "requests": 0, "seconds": 0.0}
    print("%-34s %8s %10s %8s %9s %7s" % ("file", "records", "recovered", "skipped", "requests", "sec"))
    for name in names:
        polluted = name if os.path.exists(name) else os.path.join(csv_dir, name)
        clean = os.path.join(clean_dir if not os.path.exists(name) else os.path.dirname(os.path.dirname(name)) + "/clean", os.path.basename(name))
        r = evaluate(polluted, clean, model, a.examples, a.max_lines)
        for k in total:
            total[k] += r[k]
        print("%-34s %8d %10d %8d %9d %7.1f" % (os.path.basename(name)[:34], r["records"], r["recovered"], r["skipped"], r["requests"], r["seconds"]))
        if a.v and r["stderr"]:
            print("   " + r["stderr"].strip().replace("\n", "\n   "))
    print("%-34s %8d %10d %8d %9d %7.1f" % ("TOTAL (%s)" % a.model, total["records"], total["recovered"], total["skipped"], total["requests"], total["seconds"]))
    print("records recovered: %.1f%%" % (100.0 * total["recovered"] / max(1, total["records"])))


if __name__ == "__main__":
    main()
