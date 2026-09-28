#!/usr/bin/env python3
"""Run a decision model over bench/data/decisions.jsonl and report per-decision accuracy.

Each record is one request (its questions batched, up to 256 per request); records run in parallel. Every
decision is asked with teacher-forced context, so decisions are independent and nothing is decoded.

    python3 bench/run.py --model winnow:e4b --limit 100 --workers 4 --out bench/data/winnow.jsonl
"""
import argparse, collections, json, os, random, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from csv_inhaler import question  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "decisions.jsonl")


def pairs(names, fields):
    return ", ".join("%s=%r" % (n, f) for n, f in zip(names, fields)) or "none"


def state_of(item, cell_question=False, examples=2, neighbors=False, profiles=0, texts=None, hint=False, label="Malformed text"):
    """State text. `texts` (list) batches several malformed records into one state, numbered from 1."""
    s = "CSV file with %d columns: %s. Delimiter ',', quote \".\n" % (len(item["names"]), ", ".join(item["names"]))
    if profiles:
        for c, name in enumerate(item["names"]):
            values = []
            for e in item["examples"]:
                if e[c] and e[c] not in values:
                    values.append(e[c])
            s += "Sample values of column %s: %s\n" % (name, "; ".join(repr(v[:80]) for v in values[:profiles]))
    for e in item["examples"][:examples]:
        if cell_question:
            s += "Valid parse of a row from the same file: %s\n" % pairs(item["names"], e)
        else:
            s += "Valid row from the same file: %r\n" % render(e)
    if neighbors and item["before"]:
        s += "Row before the malformed text: %r\n" % item["before"]
    if hint:
        s += "Some values in the malformed text contain commas or line breaks that were not quoted.\n"
    if texts:
        for n, t in enumerate(texts, 1):
            s += "Malformed text %d: %r\n" % (n, t)
    else:
        s += ("Currently attempting to parse: %r\n" if cell_question else label + ": %r\n") % item["text"]
    if neighbors:
        s += "Lines after the malformed text: %r\n" % "\n".join(item["after"])
    return s


def render(record):
    return ",".join(record)  # the file is written without quoting


def ask(host, model, state, questions, timeout, choice=False):
    """questions: list of (id, instructions, yes, no) -> {id: P(yes)}; raises on HTTP errors.

    With choice=True the same decision is sent as a two-option `choice` question instead of a `noul`."""
    answers = {}
    for i in range(0, len(questions), 256):
        batch = questions[i:i + 256]
        if choice:
            qs = {qid: {"type": "choice", "instructions": q, "criteria": {"yes": yes or "yes", "no": no or "no"}} for qid, q, yes, no in batch}
        else:
            qs = {qid: dict({"type": "noul", "instructions": q}, **({"criteria": {"true": yes, "false": no}} if yes else {}))
                  for qid, q, yes, no in batch}
        body = {"model": model, "state": state, "questions": qs}
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("OLLAYA_API_KEY")
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(host + "/v1/systemone", json.dumps(body).encode(), headers)
        for attempt in range(6):  # back off on rate limits and overload
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    resp = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code not in (429, 529) or attempt == 5:
                    raise
                time.sleep(2 ** attempt)
        for qid, *_ in batch:
            a = resp["answers"][qid]
            answers[qid] = a["probabilities"]["yes"] if choice else a["noul"]
    return answers


def json_state(item, a_examples=2):
    """TypeSafe-style state: named fields instead of a text blob."""
    return {"columns": item["names"],
            "valid_parses": [dict(zip(item["names"], e)) for e in item["examples"][:a_examples]],
            "currently_parsing": item["text"]}


def json_question(d, names):
    """The cell question with its context as an instructions object (TypeSafe structured instructions)."""
    boundary = "comma" if d["kind"] == "delimiter" else "line break"
    return ({"fields_read_so_far": d["fields_so_far"], "current_field": d["column"], "remaining_text": d["remaining"],
             "question": "Is %r (the text of `remaining_text` before its first %s) the complete content of `current_field`?" % (d["cell"], boundary)},
            "yes: %r is the complete content; the %s ends the field" % (d["cell"], boundary),
            "no: the content continues past the %s" % boundary)


def cell_question(d):
    """The column-name variant, as a yes/no: is the cell complete at this comma / line break?"""
    boundary = "comma" if d["kind"] == "delimiter" else "line break"
    return ("%s Current field to parse: %r. Remaining text: %r. Is %r (the text before the first %s) the complete "
            "content of %r?" % (d["so_far"].split(" Current field so far")[0], d["column"], d["remaining"], d["cell"], boundary, d["column"]),
            "yes: %r is the complete content; the %s ends the field" % (d["cell"], boundary),
            "no: the content continues past the %s" % boundary)


def run_batch(items, a):
    """Several records in one request: shared state with numbered malformed texts, questions name their row."""
    char = {"delimiter": ",", "quote": '"', "newline": "\n"}
    items = [dict(it, decisions=[d for d in it["decisions"] if d["kind"] != "quote"]) for it in items]
    qs = []
    for n, it in enumerate(items, 1):
        for k, d in enumerate(it["decisions"]):
            q, yes, no = question(d["kind"], char[d["kind"]], d["before"], d["after"])
            qs.append(("r%dq%d" % (n, k), "In malformed text %d: %s" % (n, q), yes, no))
    state = state_of(items[0], examples=a.examples, neighbors=False, profiles=a.profiles, texts=[it["text"] for it in items])
    try:
        probs = ask(a.host, a.model, state, qs, a.timeout, a.choice)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if (e.code == 422 and "STATE_TRUNCATED" in body) or (e.code == 400 and "max_tokens_exceeded" in body):
            return [{"source": it["source"], "pollution": it["pollution"], "too_long": True, "decisions": []} for it in items]
        raise SystemExit("HTTP %d: %s" % (e.code, body[:300]))
    out = []
    for n, it in enumerate(items, 1):
        ds = [dict(d, p=probs["r%dq%d" % (n, k)], correct=(probs["r%dq%d" % (n, k)] >= 0.5) == (d["truth"] == "syntax"))
              for k, d in enumerate(it["decisions"])]
        out.append({"source": it["source"], "pollution": it["pollution"], "too_long": False, "decisions": ds})
    return out


CRITERIA = {
    "default": {"delimiter": ("a field separator", "part of the text of one value"),
                "newline": ("the end of a record: the next line is a new record", "a line break inside a value: one record")},
    "rich": {"delimiter": ("a separator: the text before it completes one column's value and the text after it begins the next column's value",
                           "text inside one value: for example a name written surname first, a list, an address, or a sentence with a comma"),
             "newline": ("the end of a record: the next line starts with the first column of a new record",
                         "a line break inside one value: the next line continues the same field")},
}


def plain_question(d, item, a):
    """The plain wording with optional variations: wider context, inline marker, ordinal, criteria style."""
    char = {"delimiter": ",", "newline": "\n"}[d["kind"]]
    text = item["text"] + "\n" + (item.get("after") or [""])[0]
    p = d["pos"]
    if a.split:  # the whole line split in two around the character, as a structured instruction
        pre, post = item["text"][:p], (item["text"][p + 1:] if p < len(item["text"]) else (item.get("after") or [""])[0])
        if d["kind"] == "delimiter":
            q = {"question": "Is the comma between `pre` and `post` a separator between two fields?", "pre": pre, "post": post}
        else:
            q = {"question": "Is the line break between `pre` and `post` the end of a record, rather than a line break inside a text value?",
                 "pre": pre, "post": post}
        yes, no = CRITERIA[a.criteria if a.criteria != "none" else "default"][d["kind"]]
        return (q, None, None) if a.criteria == "none" else (q, yes, no)
    before, after = text[max(0, p - a.context):p], text[p + 1:p + 1 + a.context]
    if a.marker:
        where = "marked ⟦%s⟧ in %r" % ("," if d["kind"] == "delimiter" else "line break", text[:p] + "⟦" + ("," if d["kind"] == "delimiter" else "⏎") + "⟧" + text[p + 1:])
    else:
        where = "between %r and %r" % (before, after)
    if d["kind"] == "delimiter":
        q = "Is the ',' %s a separator between two fields?" % where
        if a.ordinal:
            n = sum(1 for x in item["decisions"] if x["kind"] == "delimiter")
            k = sum(1 for x in item["decisions"] if x["kind"] == "delimiter" and x["pos"] < p) + 1
            q = "Is the ',' %s (the %s of %d commas; a correct record has exactly %d separators) a separator between two fields?" % (
                where, run_ordinal(k), n, len(item["names"]) - 1)
    else:
        q = "Is the line break %s the end of a record, rather than a line break inside a text value?" % where
    if a.criteria == "none":
        return (q, None, None)
    yes, no = CRITERIA[a.criteria][d["kind"]]
    return (q, yes, no)


def run_ordinal(n):
    return "%d%s" % (n, "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def run_item(item, a):
    char = {"delimiter": ",", "quote": '"', "newline": "\n"}
    if not a.quotes:
        item = dict(item, decisions=[d for d in item["decisions"] if d["kind"] != "quote"])
    if a.cell_question or a.json:
        make = (lambda d: json_question(d, item["names"])) if a.json else cell_question
        qs = [("q%d" % k, *make(d)) for k, d in enumerate(item["decisions"])]
    else:
        qs = []
        for k, d in enumerate(item["decisions"]):
            q, yes, no = plain_question(d, item, a)
            if a.so_far and isinstance(q, str):
                q += " " + d["so_far"]
            qs.append(("q%d" % k, q, yes, no))
    try:
        probs = ask(a.host, a.model, json_state(item, a.examples) if a.json else
                    state_of(item, a.cell_question, a.examples, a.neighbors, a.profiles, hint=a.hint, label=a.label), qs, a.timeout, a.choice)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if (e.code == 422 and "STATE_TRUNCATED" in body) or (e.code == 400 and "max_tokens_exceeded" in body):
            return {"source": item["source"], "pollution": item["pollution"], "too_long": True, "decisions": []}
        raise SystemExit("HTTP %d: %s" % (e.code, body[:300]))
    out = []
    for k, d in enumerate(item["decisions"]):
        p = probs["q%d" % k]
        out.append(dict(d, p=p, correct=(p >= 0.5) == (d["truth"] == "syntax")))
    return {"source": item["source"], "pollution": item["pollution"], "too_long": False, "decisions": out}


def report(results, model, seconds):
    acc = collections.defaultdict(lambda: [0, 0])  # key -> [right, n]
    records = collections.defaultdict(lambda: [0, 0])
    too_long = collections.Counter()
    for r in results:
        if r["too_long"]:
            too_long[r["source"]] += 1
            continue
        records[r["source"]][1] += 1
        records[r["source"]][0] += all(d["correct"] for d in r["decisions"])
        for d in r["decisions"]:
            for key in ((r["source"], d["kind"], d["truth"]), ("ALL", d["kind"], d["truth"]), ("ALL", d["kind"], "both"), ("ALL", "all", "both")):
                acc[key][0] += d["correct"]; acc[key][1] += 1
    print("\n%s  (%.0f s)" % (model, seconds))
    print("%-10s %-9s %-7s %8s %7s" % ("source", "decision", "truth", "n", "acc"))
    for key in sorted(acc, key=lambda k: (k[0] != "ALL", k)):
        right, n = acc[key]
        print("%-10s %-9s %-7s %8d %6.1f%%" % (key[0], key[1], key[2], n, 100.0 * right / n))
    for src in sorted(set(records) | set(too_long)):
        ok, n = records[src]
        print("%-10s records with every decision right: %d of %d (%.0f%%)%s" % (
            src, ok, n, 100.0 * ok / max(1, n), "; %d records too long for the model" % too_long[src] if too_long[src] else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="laya:en")
    ap.add_argument("--host", default=os.environ.get("OLLAYA_HOST", "http://127.0.0.1:11435").rstrip("/"),
                    help="Ollaya server, or https://api.typesafe.ai with TYPESAFE_API_KEY set and --model jev-latest")
    ap.add_argument("--limit", type=int, default=None, help="records per source")
    ap.add_argument("--pollution", choices=["unquoted", "unescaped"], default=None)
    ap.add_argument("--so-far", action="store_true", help="append the teacher-forced parse-so-far to each question")
    ap.add_argument("--examples", type=int, default=2, help="clean rows of the same file shown in the state (up to 30)")
    ap.add_argument("--context", type=int, default=14, help="characters of context on each side of the questioned character")
    ap.add_argument("--split", action="store_true", help="structured instructions: the line split into `pre` and `post` around the character")
    ap.add_argument("--marker", action="store_true", help="show the whole record with the questioned character marked")
    ap.add_argument("--ordinal", action="store_true", help="state the comma's ordinal and the expected separator count")
    ap.add_argument("--criteria", choices=["default", "rich", "none"], default="default")
    ap.add_argument("--label", default="Malformed text", help="label of the record line in the state")
    ap.add_argument("--hint", action="store_true", help="state that some values contain unquoted commas or line breaks")
    ap.add_argument("--neighbors", action="store_true", help="show the row before and the two lines after the malformed text")
    ap.add_argument("--profiles", type=int, default=0, help="show up to N sample values per column")
    ap.add_argument("--rows-per-request", type=int, default=1, help="batch several records of one source into a request")
    ap.add_argument("--quotes", action="store_true", help="also ask about quote characters (off by default: quotes are a rule)")
    ap.add_argument("--choice", action="store_true", help="send each decision as a two-option choice instead of a noul")
    ap.add_argument("--json", action="store_true", help="the cell question with JSON-object state and instructions (TypeSafe style)")
    ap.add_argument("--cell-question", action="store_true",
                    help="ask 'is this the complete value of column X?' with the fields so far; no quote questions")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=None, help="write per-decision results here (jsonl)")
    a = ap.parse_args()
    if "://" not in a.host:
        a.host = "http://" + a.host

    items = [json.loads(l) for l in open(DATA, encoding="utf-8")]
    if a.pollution:
        items = [it for it in items if it["pollution"] == a.pollution]
    if a.limit:
        rng, picked = random.Random(a.seed), []
        for src in sorted({it["source"] for it in items}):
            pool = [it for it in items if it["source"] == src]
            picked += rng.sample(pool, min(a.limit, len(pool)))
        items = picked
    t0 = time.time()
    total = sum(len(it["decisions"]) for it in items)
    done = [0, 0]

    def work(it):
        r = run_item(it, a)
        done[0] += 1; done[1] += len(it["decisions"])
        if done[0] % 20 == 0:
            sys.stderr.write("%d/%d records, %d/%d decisions, %.0f s\n" % (done[0], len(items), done[1], total, time.time() - t0))
        return r

    if a.rows_per_request > 1:
        groups = []
        for src in sorted({it["source"] for it in items}):
            pool_items = [it for it in items if it["source"] == src]
            groups += [pool_items[i:i + a.rows_per_request] for i in range(0, len(pool_items), a.rows_per_request)]
        with ThreadPoolExecutor(a.workers) as pool:
            results = [r for rs in pool.map(lambda g: run_batch(g, a), groups) for r in rs]
    else:
        with ThreadPoolExecutor(a.workers) as pool:
            results = list(pool.map(work, items))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report(results, a.model + (" +so-far" if a.so_far else "") + (" +cell-question" if a.cell_question else "") + (" +choice" if a.choice else "") + (" +json" if a.json else "") + (" +quotes" if a.quotes else "") + " examples=%d" % a.examples + (" +neighbors" if a.neighbors else "")
           + (" profiles=%d" % a.profiles if a.profiles else "") + (" rows/request=%d" % a.rows_per_request if a.rows_per_request > 1 else "")
           + " context=%d" % a.context + (" +marker" if a.marker else "") + (" +ordinal" if a.ordinal else "")
           + (" criteria=%s" % a.criteria if a.criteria != "default" else "") + (" +hint" if a.hint else "")
           + (" label=%r" % a.label if a.label != "Malformed text" else "") + (" +split" if a.split else ""),
           time.time() - t0)


if __name__ == "__main__":
    main()
