# csv-inhaler

Clean a dirty CSV file with a local decision model. 

Requires a running [Ollaya](https://ollaya.dev/docs/quickstart) server with a model pulled.
If it is not on `127.0.0.1:11435`, set `OLLAYA_HOST` or pass `--host`.

```sh
csv-inhaler dirty.csv > clean.csv
cat dirty.csv | csv-inhaler --model winnow:e4b --log decisions.jsonl > clean.csv
```

Each line is parsed with Python's `csv` module in strict mode. Lines that parse with the right number of
fields are written out unchanged. When a line does not
parse, an [Ollaya](https://ollaya.dev) decision model is asked one yes/no question at a time:

1. **Does the next line belong to this record?** For each following line: "Is the line break between
   `…` and `…` the end of a record, rather than a line break inside a text value?" Lines are joined while
   the model says the break is inside a value (up to `--max-lines`).
2. **Character by character:** every delimiter outside quotes gets "Is the ',' between `…` and `…` a
   separator between two fields?", and every quote gets "Is the quote between `…` and `…` CSV syntax,
   opening or closing a quoted value, rather than a literal character of the text?" A quote judged syntax
   toggles the quoted state; inside quotes, delimiters are text without asking. Each question carries the
   fields read so far and the current field so far, so later decisions see earlier ones.

## Options

| option | default | meaning |
|---|---|---|
| `input` | stdin | file or `-` |
| `--model` | `laya:en` | Ollaya model; `winnow:e4b` decides much better, `laya:en` answers in milliseconds |
| `--host` | `$OLLAYA_HOST` or `http://127.0.0.1:11435` | Ollaya server; `$OLLAYA_API_KEY` is sent if set |
| `-d`, `-q` | sniffed, `"` | delimiter (most frequent of `,` `;` tab `|` in the header) and quote |
| `--names a,b,c` | | column names for a file without a header |
| `--examples` | 3 | clean rows shown to the model |
| `--max-lines` | 50 | most physical lines joined into one record |
| `--log FILE` | | JSON line per repaired record: text, fields, every decision with its probability |

## Benchmark

The same questions make a benchmark for decision models. `bench/data/decisions.jsonl` holds 860 records from
clean public data (Project Gutenberg catalog, NYC job postings, Hacker News comments) written the way a
generator that never quotes would write them: cells joined by commas, no quoting, no escaping. Every comma and
line break is a labelled decision, 32,571 in all. Every decision is asked with ground-truth context, so a
record's questions go in one batched request and records run in parallel.

```sh
python3 bench/run.py --host https://api.typesafe.ai --model jev-latest --limit 100 --workers 6 \
    --examples 0 --profiles 20 --label "Attempting to parse" --out bench/data/jev2_noul.jsonl
```

Jev reaches 98% of decisions with that configuration (about $0.08 and 15 s per run); see `HANDOFF.md` for
the results table, what was tried, and what failed. `bench/build.py` rebuilds the dataset from the live
sources, which drift, so the committed file is the benchmark.
