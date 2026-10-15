# csv-audit

**Know what's wrong with a CSV before you load it.** Point it at a file; it profiles every column and reports the concrete, checkable problems — ragged rows, mixed types, five spellings of "missing", dates that disagree, stray whitespace, outliers — with the column and a fix.

No dependencies. No silent edits. Just an honest diagnosis.

```console
$ csv-audit people.csv
# csv-audit report

**6 row(s) x 6 column(s)** — 3 finding(s): 0 high, 0 medium, 3 low, 0 info

## Column profile

| # | column  | type   | missing | unique |
|---|---------|--------|---------|--------|
| 0 | id      | int    | 0 (0%)  | 6      |
| 3 | joined  | date   | 1 (17%) | 5      |
| 4 | country | string | 0 (0%)  | 3      |
...

### `CSV009` [LOW] [joined]

dates are written in 2 different formats (YYYY-MM-DD, DD/MM/YYYY)

> Standardize on one format (ISO-8601 sorts and parses everywhere).
```

## Why

Most "the import broke" bugs are one of a handful of things: a row with the wrong number of fields, a numeric column that isn't fully numeric, or `""` and `N/A` and `-` all meaning "missing" in the same file. csv-audit finds those in one pass, deterministically, on a single file — before they reach your pipeline.

## Install

No install. It's a single standard-library script.

```bash
git clone https://github.com/muguang1224/csv-audit
python3 csv-audit/scripts/csv_audit.py /path/to/data.csv
```

Requires Python 3.8+.

## Usage

```bash
csv_audit.py FILE [--format markdown|json] [--fail-on LEVEL]
             [--delimiter D] [--no-header] [--max-rows N]
             [--missing-threshold F] [--clean OUT] [--dedupe]
             [--ignore RULE ...] [--version]
```

| Flag | Meaning |
|------|---------|
| `FILE` | CSV/TSV to audit |
| `--format` | `markdown` (default) or `json` |
| `--fail-on` | Exit `1` if any finding is at/above `info\|low\|medium\|high` |
| `--delimiter` | Field delimiter (default: sniffed; `.tsv` uses tab) |
| `--no-header` | Treat the first row as data |
| `--max-rows` | Read at most N data rows |
| `--missing-threshold` | Flag columns at/above this missing ratio (default `0.5`) |
| `--clean OUT` | Write a trimmed, null-normalized copy |
| `--dedupe` | With `--clean`, also drop exact duplicate rows |
| `--ignore` | Silence a rule id (`CSV002`); repeatable |

Exit codes: `0` clean / below threshold · `1` finding at/above `--fail-on` · `2` usage or IO error.

## Rules

| Rule | Severity | Meaning |
|------|----------|---------|
| CSV011 | high | Row has the wrong field count |
| CSV001 | medium | Duplicate or empty column name |
| CSV003 | medium | Column is mostly (or entirely) empty |
| CSV004 | medium | Duplicate rows |
| CSV005 | medium | Column mixes numeric and non-numeric values |
| CSV006 | low | Missing values use several spellings |
| CSV007 | low | Numeric values outside the IQR fence |
| CSV008 | low | Leading/trailing whitespace |
| CSV009 | low | More than one date format in a column |
| CSV013 | low | Values differ only by case |
| CSV002 | info | Column has a single value |
| CSV010 | info | Every value is unique (ID-like) |
| CSV012 | info | UTF-8 BOM present |
| CSV000 | info | File has no rows |

## CI

```yaml
- run: python3 csv-audit/scripts/csv_audit.py data/export.csv --fail-on medium
```

## What it is not

Not a transformer, not a joiner, not a statistics engine. `--clean` only trims whitespace, normalizes null spellings to empty, and optionally drops exact duplicate rows — nothing semantic. See `SKILL.md` for the full guardrails.

## Also an Agent Skill

This repo is also a self-contained Agent Skill: [`SKILL.md`](SKILL.md) contains the workflow, the guardrails, and the full script inline.

## License

MIT — see [`LICENSE`](LICENSE).
