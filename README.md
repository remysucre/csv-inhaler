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
