# Handoff notes — csv-inhaler benchmark (2026-09-28)

## What this is now

A benchmark for **decision models** (TypeSafe Jev, Ollaya's laya/winnow/kev) on CSV framing: given a record
written with no quoting at all, decide for every comma and line break whether it is syntax (a field separator /
the end of the record) or text. The cleaning tool (`csv_inhaler.py`) still exists and works with Jev, but the
benchmark is the focus.

Everything below is **uncommitted**: the only commit is the original DuckDB scaffold. First thing to do on any
machine is `git add -A && git commit`.

## Layout

| path | what |
|---|---|
| `bench/build.py` | builds `bench/data/decisions.jsonl` from clean public data; downloads are cached in `bench/data/raw/` (gitignored) |
| `bench/run.py` | runs a model over the dataset in parallel, prints the report, saves per-decision results as jsonl |
| `bench/data/decisions.jsonl` | the dataset: 866 records, 33,834 labelled decisions |
| `bench/data/jev2_*.jsonl` | results on the current dataset (older `jev_*`, `laya*`, `winnow*` files are from the previous, one-cell pollution and are not comparable) |
| `csv_inhaler.py`, `tests/`, `eval/` | the tool, its offline tests, and end-to-end evaluations of the tool on Pollock files |
| `README.md` | describes the tool; its "Benchmark" section still describes the old one-cell pollution and needs rewriting |

## The dataset

Sources, all downloaded without login: Project Gutenberg catalog (authors `Surname, Given, dates`, multi-line
titles), NYC job postings via Socrata (30 columns, long text, addresses), Hacker News comments via the Algolia
API (paragraphs, quotes, code). `build.py` samples records that have at least one cell containing a comma, quote
or line break, and writes each record as `",".join(cells)`: no quoting, no escaping. Labels come from the clean
record. Every comma and line break is a decision; quotes are never asked about.

Deterministic for a given `--seed` (default 1). `--per-source 300` was used; the runs so far use the first 100
records per source that `run.py --limit 100` draws (also seeded), so 200 per source are untouched.

```
python3 bench/build.py --per-source 300
```

## Running

Jev (hosted): put `TYPESAFE_API_KEY` in the environment. `$0.042` per million input tokens; a 100-per-source run is
about 15 seconds and under a cent.

```
python3 bench/run.py --host https://api.typesafe.ai --model jev-latest --limit 100 --workers 6 \
    --examples 0 --profiles 20 --label "Attempting to parse" --out bench/data/jev2_noul.jsonl
python3 bench/run.py ... --choice --out bench/data/jev2_choice.jsonl      # same, as choice questions
```

Ollaya (local or a GPU box): `--host http://<host>:11435`, `--model winnow:e4b`. The runner also honours
`OLLAYA_HOST`. Laya cannot be used: its 512-token context rejects nearly every record.

On a CUDA box:
```
docker run -d --gpus=all -p 11435:11435 -v ollaya:/root/.ollaya ghcr.io/ollaya-dev/ollaya:cuda
curl -X POST http://localhost:11435/api/pull -d '{"model":"winnow:e4b"}'    # 8 GB, Q8; fits 12 GB VRAM
python3 bench/run.py --host http://localhost:11435 --model winnow:e4b --limit 30 --workers 4 \
    --examples 0 --profiles 20 --label "Attempting to parse" --out bench/data/winnow2_noul.jsonl
```
Winnow on the M5 does about 3 decisions per second with 4 workers; `--limit 30` is 3,780 decisions. Requests
are queued server-side, so more workers do not help unless the server batches. `kev:9b` (Ollaya's most
accurate) is ~10 GB at Q8 and may not fit 12 GB with context; untested.

Report columns: accuracy per source × decision kind × truth class, plus records with every decision right.
Records over the model's token limit are counted separately, never as errors.

## Winning configuration (Jev)

- `noul` questions, or `choice` and average the two probabilities offline (see below).
- State: schema line, **20 sample values per column** (no example rows), the record labelled
  **"Attempting to parse:"**.
- Question: the plain wording with **14 characters of context** each side, default criteria.

Results on the current dataset, 100 records per source:

| config | overall | separators | textual commas | line breaks | records Gut / HN / NYC |
|---|---|---|---|---|---|
| noul | 98.2% | 98.3% | 98.0% | 100% | 53 / 79 / 34 |
| choice | 97.9% | 95.7% | 99.1% | 100% | 59 / 82 / 26 |
| mean(choice, noul) | 98.2% | 97.0% | 98.7% | 100% | 59 / 83 / 32 |

Ensemble is computed offline from the two saved jsonl files (same records, same order):
```python
import json
c=[json.loads(l) for l in open('bench/data/jev2_choice.jsonl')]; n=[json.loads(l) for l in open('bench/data/jev2_noul.jsonl')]
for rc, rn in zip(c, n):
    for dc, dn in zip(rc['decisions'], rn['decisions']):
        p = (dc['p'] + dn['p']) / 2   # decide syntax if p >= 0.5; dc['truth'] is the label
```
Run-to-run noise on Jev: overall accuracy stable to 0.1 points; whole-record counts wobble ±3 per source.

## What helped and what hurt (all measured, `bench/run.py` flags)

Helped: removing quote questions entirely (`--quotes` brings them back); column profiles instead of example
rows (`--profiles 20 --examples 0`); "Attempting to parse" instead of "Malformed text" (`--label`); `--choice`
for textual commas, `noul` for separators; averaging the two.

Hurt, every time: more example rows (`--examples 10/30`); wider context (`--context 30/60`); the whole record
with a marker (`--marker`); neighbouring rows (`--neighbors`); a prior sentence (`--hint`); richer or no
criteria (`--criteria rich|none`); ordinal/count in the question (`--ordinal`); the column-name / parse-so-far
question forms (`--cell-question`, `--json`, `--so-far`); the line split into `pre`/`post` (`--split`);
several records per request (`--rows-per-request 5`). The pattern: showing Jev more of the line makes it
worse at spotting textual commas. Small local context wins.

## Remaining failure shapes (Jev)

1. Separators next to empty fields or quote characters (`', 3 FL, LIC NY' | ',"New York Cit'`) — NYC Jobs, the largest group.
2. Commas inside addresses with `",,"` nearby — the mirror image.
3. `Surname, Given` authors right after `,en,` — nearly all of Gutenberg's errors.
4. The bare title/comment boundary on Hacker News.

## Other facts worth knowing

- Laya answers the polarity of the question, not the content; winnow (on the old dataset) was ~74% and
  inverted on quotes. A winnow run on the current dataset was started on the M5 at the time of writing
  (`bench/data/winnow2_noul.jsonl` when done).
- TypeSafe skill is installed (`claude plugin install typesafe@typesafe-ai`); its docs are at
  docs.typesafe.ai (`*.md` suffix for Markdown). Structure-recovery cookbook is the closest published pattern.
- Ollaya on this Mac is the desktop app at 127.0.0.1:11435; the CLI is inside the app bundle.
- Jev limits: 32k tokens for state + longest question, 64k per request; exceeding returns HTTP 400
  `max_tokens_exceeded`, which the runner counts as "too long".

## To do

- Commit. Rewrite the README benchmark section for whole-record pollution. Add a `bench/README.md` dataset
  card and a summary script that turns result files into the table above.
- Use the remaining 200 records per source as a held-out set once the configuration is frozen.
- Try `kev:9b` on a GPU box; try Jev with per-source thresholds; try a cleaned context snippet for failure
  shapes 1–2 (strip adjacent quote characters) and measure.
