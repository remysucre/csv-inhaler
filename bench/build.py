#!/usr/bin/env python3
"""Build the CSV-framing decision benchmark from clean public data.

Sources (downloaded once into bench/data/raw/):
  gutenberg  Project Gutenberg catalog: authors like "Austen, Jane, 1775-1817", multi-line titles
  nycjobs    NYC job postings via Socrata: 30 columns of long text with commas and quotes
  hn         Hacker News comments via the Algolia API: paragraphs, quotes, code

Every sampled record is written the way a generator that never quotes would write it: cells joined by the
delimiter with no quoting and no escaping, so every quote character in the text is literal and every comma or
line break inside a value is bare. Every comma and line break in the result is a labelled decision: syntax
(separator / end of record) or text. Quotes are never a decision.

    python3 bench/build.py --per-source 300   ->  bench/data/decisions.jsonl
"""
import argparse, csv, html, io, json, os, random, re, sys, urllib.request

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "decisions.jsonl")
SPECIAL = (",", '"', "\n")


def fetch(name, url):
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, name)
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": "csv-inhaler-bench"})
        with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
            f.write(r.read())
    return path


def gutenberg():
    rows = list(csv.reader(open(fetch("pg_catalog.csv", "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"),
                                encoding="utf-8", newline="")))
    return rows[0], [[c.replace("\r\n", "\n") for c in r] for r in rows[1:]]


def nycjobs():
    rows = list(csv.reader(open(fetch("nycjobs.csv", "https://data.cityofnewyork.us/resource/kpav-sd4t.csv?$limit=2000"),
                                encoding="utf-8", newline="")))
    return rows[0], rows[1:]


def hn():
    hits = json.load(open(fetch("hn.json", "https://hn.algolia.com/api/v1/search_by_date?tags=comment&hitsPerPage=1000")))["hits"]
    rows = []
    for h in hits:
        text = h.get("comment_text") or ""
        text = re.sub(r"<p>", "\n\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        if text:
            rows.append([h["objectID"], h.get("author") or "", h.get("created_at") or "", h.get("story_title") or "", text])
    return ["id", "author", "created_at", "story_title", "comment"], rows


def render(record):
    """Render one record without any quoting and label every comma and line break in it."""
    out, decisions = [], []
    for j, cell in enumerate(record):
        if j > 0:
            decisions.append({"kind": "delimiter", "pos": len("".join(out)), "truth": "syntax"})
            out.append(",")
        start = len("".join(out))
        for k, ch in enumerate(cell):
            if ch == ",":
                decisions.append({"kind": "delimiter", "pos": start + k, "truth": "text"})
            elif ch == "\n":
                decisions.append({"kind": "newline", "pos": start + k, "truth": "text"})
        out.append(cell)
    return "".join(out), decisions


def so_far(record, text, pos):
    """Teacher-forced parse context at `pos`: completed fields and the current field's text so far."""
    consumed = 0
    for j, cell in enumerate(record):
        end = consumed + len(cell)
        if pos <= end:
            return record[:j], cell[:pos - consumed]
        consumed = end + 1  # the delimiter
    return record[:-1], record[-1]


def build(source, names, rows, n, rng):
    items = []
    candidates = [(i, j) for i, r in enumerate(rows) if len(r) == len(names) for j, c in enumerate(r)
                  if any(ch in c for ch in SPECIAL) and len(c) < 600]
    seen = set()
    for i, j in rng.sample(candidates, min(n, len(candidates))):
        if i in seen:
            continue  # one polluted copy per record
        seen.add(i)
        record = rows[i]
        for pollution in ["unquoted"]:
            text, decisions = render(record)
            nxt = render(rows[(i + 1) % len(rows)])[0]
            decisions.append({"kind": "newline", "pos": len(text), "truth": "syntax"})  # the break to the next record
            for d in decisions:
                p = d["pos"]
                full = text + "\n" + nxt
                d["before"], d["after"] = full[max(0, p - 14):p], full[p + 1:p + 15]
                d["after_long"] = full[p + 1:p + 81]  # look-ahead for the cell question
                fields, cur = so_far(record, text, p)
                d["so_far"] = "Fields read so far: %s. Current field so far: %r." % (
                    ", ".join("%s=%r" % (nm, f) for nm, f in zip(names, fields)) or "none", cur)
                d["fields_so_far"] = dict(zip(names, fields))
                d["column"] = names[min(len(fields), len(names) - 1)]  # the column this cell belongs to
                d["cell"] = cur  # the cell's text up to this point
                d["remaining"] = full[p - len(cur):p - len(cur) + 240]  # from the start of this cell onwards
            examples = [rows[k] for k in rng.sample(range(len(rows)), 31) if k != i][:30]  # clean records
            items.append({"source": source, "pollution": pollution, "names": names, "record": record, "cell": j,
                          "text": text, "examples": examples, "decisions": decisions,
                          "before": render(rows[i - 1])[0] if i > 0 else "",
                          "after": [render(rows[(i + k) % len(rows)])[0] for k in (1, 2)]})
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-source", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    items = []
    for source, loader in [("gutenberg", gutenberg), ("nycjobs", nycjobs), ("hn", hn)]:
        names, rows = loader()
        built = build(source, names, rows, a.per_source, rng)
        print("%-10s %6d rows -> %4d records, %6d decisions" % (source, len(rows), len(built), sum(len(x["decisions"]) for x in built)))
        items += built
    with open(OUT, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
