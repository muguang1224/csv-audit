# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-15

### Added

- CSV/TSV data-quality profiler. Delimiter is sniffed (tab for `.tsv`) unless
  overridden, and a UTF-8 BOM is handled.
- Rules `CSV000`–`CSV013` with stable ids and severities: ragged rows, duplicate
  or empty headers, mostly- or entirely-empty columns, duplicate rows, mixed
  types, inconsistent null spellings, inconsistent date formats, stray
  whitespace, case-inconsistent categories, IQR outliers, constant columns,
  ID-like columns, and BOM.
- Per-column type inference (`int`, `float`, `bool`, `date`, `string`, `mixed`,
  `null`) plus missing / unique / range statistics.
- `--clean OUT` writes a trimmed, null-normalized copy; `--dedupe` also drops
  exact duplicate rows.
- `--format markdown|json`, `--fail-on LEVEL`, `--delimiter`, `--no-header`,
  `--max-rows`, `--missing-threshold`, `--ignore RULE`.
- Self-contained `SKILL.md` with the script inlined, so the whole skill is one file.

### Fixed

- An empty file crashed in the default markdown output (`KeyError: 'cells'`); it
  now reports the documented `CSV000` finding and exits `0`, matching `--format json`.
- A blank line was reported as a ragged row at high severity, so a hand-edited or
  concatenated file would fail a `--fail-on high` gate; blank lines are now ignored.
- `--clean` rewrote line endings from LF to CRLF as a side effect of the default
  `csv.writer` terminator.

[1.0.0]: https://github.com/muguang1224/csv-audit/releases/tag/v1.0.0
