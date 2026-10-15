#!/usr/bin/env python3
"""csv-audit -- profile a CSV/TSV file and report data-quality problems.

Standard library only (Python 3.8+). Infers column types, measures missing and
duplicate values, and flags the concrete issues that bite downstream: ragged
rows, mixed types, inconsistent null spellings, ragged dates, stray whitespace,
numeric outliers, duplicate headers, and more. Optionally writes a cleaned copy.

Usage:
    python3 csv_audit.py FILE [--format markdown|json] [--fail-on LEVEL]
                        [--delimiter D] [--ignore RULE ...] [--max-rows N]
                        [--missing-threshold F] [--clean OUT] [--dedupe] [--top N]

Exit codes:
    0  ran clean, or no finding at/above --fail-on
    1  at least one finding at/above --fail-on
    2  usage / IO error
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

VERSION = "1.0.0"
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}

INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
BOOL_RE = re.compile(r"^(true|false|yes|no)$", re.IGNORECASE)

NULL_TOKENS = {"", "na", "n/a", "null", "none", "nil", "nan", "-", "--", "?",
               "unknown", "n.a.", "#n/a", "n\\a"}

DATE_FORMATS = [
    ("%Y-%m-%d", "YYYY-MM-DD"),
    ("%Y/%m/%d", "YYYY/MM/DD"),
    ("%Y-%m-%dT%H:%M:%S", "ISO-8601 datetime"),
    ("%Y-%m-%d %H:%M:%S", "YYYY-MM-DD HH:MM:SS"),
    ("%d/%m/%Y", "DD/MM/YYYY"),
    ("%m/%d/%Y", "MM/DD/YYYY"),
    ("%d-%m-%Y", "DD-MM-YYYY"),
    ("%b %d, %Y", "Mon DD, YYYY"),
]


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------
class Finding:
    __slots__ = ("rule", "severity", "column", "message", "hint")

    def __init__(self, rule, severity, column, message, hint=""):
        self.rule = rule
        self.severity = severity
        self.column = column
        self.message = message
        self.hint = hint

    def key(self):
        return (self.rule, self.column, self.message)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def sniff_delimiter(sample, path):
    if path.lower().endswith(".tsv"):
        return "\t"
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def read_table(path, delimiter=None, has_header=True, max_rows=None):
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        delim = delimiter or sniff_delimiter(sample, path)
        reader = csv.reader(fh, delimiter=delim)
        rows = []
        for i, row in enumerate(reader):
            rows.append(row)
            if max_rows and i + 1 >= max_rows:
                break
    if not rows:
        return [], [], delim
    header = rows[0] if has_header else ["col%d" % i for i in range(len(rows[0]))]
    body = rows[1:] if has_header else rows
    body = [r for r in body if len(r) > 0]   # ignore blank lines (not a data row)
    return header, body, delim


def has_bom(path):
    with open(path, "rb") as fh:
        return fh.read(3) == b"\xef\xbb\xbf"


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------
def classify(value):
    """Return one of: null, int, float, bool, date, string."""
    v = value.strip()
    if v.lower() in NULL_TOKENS:
        return "null"
    if INT_RE.match(v):
        return "int"
    if FLOAT_RE.match(v):
        return "float"
    if BOOL_RE.match(v):
        return "bool"
    if match_date(v)[0]:
        return "date"
    return "string"


def match_date(v):
    for fmt, name in DATE_FORMATS:
        try:
            datetime.strptime(v, fmt)
            return True, name
        except ValueError:
            continue
    return False, None


def numeric_value(v):
    v = v.strip()
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Column profiling
# ---------------------------------------------------------------------------
def profile_column(name, values):
    """Return a dict profile of one column plus a list of Finding."""
    findings = []
    non_null = [v for v in values if v.strip().lower() not in NULL_TOKENS]
    null_cells = len(values) - len(non_null)
    types = Counter(classify(v) for v in values)

    # infer column type
    present = {t for t in types if t != "null"}
    if not present:
        ctype = "null"
    elif present <= {"int"}:
        ctype = "int"
    elif present <= {"int", "float"}:
        ctype = "float"
    elif present <= {"bool"}:
        ctype = "bool"
    elif present <= {"date"}:
        ctype = "date"
    elif len(present) == 1:
        ctype = present.pop()
    else:
        ctype = "mixed"

    profile = {
        "name": name, "type": ctype, "count": len(values),
        "missing": null_cells,
        "missing_ratio": round(null_cells / len(values), 3) if values else 0.0,
        "unique": len(set(values)),
    }

    # numeric stats
    nums = [numeric_value(v) for v in non_null]
    nums = [n for n in nums if n is not None]
    if nums and ctype in ("int", "float"):
        profile["min"] = min(nums)
        profile["max"] = max(nums)
        profile["mean"] = round(sum(nums) / len(nums), 4)

    # CSV005 mixed types (numeric majority with some non-numeric)
    numeric_cells = [v for v in non_null if classify(v) in ("int", "float")]
    non_numeric = [v for v in non_null if classify(v) not in ("int", "float")]
    if numeric_cells and non_numeric and len(numeric_cells) / len(non_null) >= 0.5:
        sample = ", ".join(repr(x) for x in non_numeric[:3])
        findings.append(Finding("CSV005", "medium", name,
                                "%d of %d non-empty values are numeric but %d are not (e.g. %s)"
                                % (len(numeric_cells), len(non_null), len(non_numeric), sample),
                                "Normalize the column to one type (strip units, fix typos)."))

    # CSV006 inconsistent null spellings
    null_spellings = Counter(v.strip() for v in values if v.strip().lower() in NULL_TOKENS)
    if len(null_spellings) >= 2:
        spellings = ", ".join(repr(k) for k in list(null_spellings)[:4])
        findings.append(Finding("CSV006", "low", name,
                                "missing values use %d different spellings (%s)"
                                % (len(null_spellings), spellings),
                                "Pick one null representation (ideally empty) and use it everywhere."))

    # CSV009 inconsistent date formats
    if ctype == "date" or any(match_date(v)[0] for v in non_null):
        formats = Counter(match_date(v)[1] for v in non_null if match_date(v)[0])
        if len(formats) >= 2:
            findings.append(Finding("CSV009", "low", name,
                                    "dates are written in %d different formats (%s)"
                                    % (len(formats), ", ".join(f for f, _ in formats.most_common())),
                                    "Standardize on one format (ISO-8601 sorts and parses everywhere)."))

    # CSV008 stray whitespace
    ws = sum(1 for v in values if v != v.strip() and v.strip() != "")
    if ws:
        findings.append(Finding("CSV008", "low", name,
                                "%d value(s) have leading/trailing whitespace" % ws,
                                "Trim on load; run with --clean to write a trimmed copy."))

    # CSV013 inconsistent casing of categories
    if ctype == "string":
        lowered = Counter(v.strip().lower() for v in non_null)
        if len(lowered) < len(set(v.strip() for v in non_null)):
            findings.append(Finding("CSV013", "low", name,
                                    "values differ only by letter case (e.g. %r vs %r)"
                                    % (_case_pair(non_null) or ("?", "?")),
                                    "Normalize case before grouping/joining on this column."))

    # CSV002 constant column
    if profile["unique"] == 1 and len(values) > 1 and ctype != "null":
        findings.append(Finding("CSV002", "info", name,
                                "column has a single value (%r) across all rows" % values[0],
                                "Drop it if it carries no information."))

    # CSV007 numeric outliers (IQR)
    if nums and len(nums) >= 5:
        q1, q3 = _percentile(nums, 25), _percentile(nums, 75)
        iqr = q3 - q1
        if iqr > 0:
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            out = [n for n in nums if n < lo or n > hi]
            if out:
                findings.append(Finding("CSV007", "low", name,
                                        "%d value(s) fall outside the IQR fence [%s, %s] (min %s, max %s)"
                                        % (len(out), _fmt(lo), _fmt(hi), _fmt(min(out)), _fmt(max(out))),
                                        "Check for data-entry errors or genuinely heavy tails."))

    # CSV010 high cardinality (likely an identifier)
    if ctype == "string" and len(values) >= 20 and profile["unique"] == len(values):
        findings.append(Finding("CSV010", "info", name,
                                "every value is unique (%d rows) -- looks like an ID column" % len(values),
                                "Exclude ID-like columns from distribution analysis."))

    return profile, findings


def _case_pair(values):
    seen = {}
    for v in values:
        k = v.strip().lower()
        if k in seen and seen[k] != v.strip():
            return (seen[k], v.strip())
        seen[k] = v.strip()
    return None


def _percentile(nums, pct):
    s = sorted(nums)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _fmt(x):
    return ("%g" % x) if isinstance(x, float) else str(x)


# ---------------------------------------------------------------------------
# Table-level analysis
# ---------------------------------------------------------------------------
def analyze(path, delimiter=None, has_header=True, max_rows=None, missing_threshold=0.5):
    findings = []
    header, body, delim = read_table(path, delimiter, has_header, max_rows)

    if not header:
        findings.append(Finding("CSV000", "info", None, "file is empty", "Nothing to profile."))
        empty = {"rows": 0, "cols": 0, "delimiter": delim, "cells": 0,
                 "missing_cells": 0, "columns": []}
        return empty, findings, header, body, delim

    # CSV001 duplicate/blank header names
    seen = Counter(header)
    for h, c in seen.items():
        if c > 1:
            findings.append(Finding("CSV001", "medium", h,
                                    "duplicate column name %r appears %d times" % (h, c),
                                    "Give each column a unique name."))
    for i, h in enumerate(header):
        if not h.strip():
            findings.append(Finding("CSV001", "medium", None,
                                    "column %d has an empty header" % i,
                                    "Name every column; unnamed columns break downstream tools."))

    # CSV011 ragged rows
    ragged = [(i + 2, len(r)) for i, r in enumerate(body) if len(r) != len(header)]
    if ragged:
        first = ragged[0]
        findings.append(Finding("CSV011", "high", None,
                                "%d row(s) have a field count different from the header (row %d has %d, expected %d)"
                                % (len(ragged), first[0], first[1], len(header)),
                                "Fix quoting or delimiters so every row has %d fields." % len(header)))

    # CSV004 duplicate rows
    if body:
        rowkeys = [tuple(r) for r in body]
        dup = len(rowkeys) - len(set(rowkeys))
        if dup:
            findings.append(Finding("CSV004", "medium", None,
                                    "%d duplicate row(s) out of %d" % (dup, len(body)),
                                    "De-duplicate, or run with --dedupe to write a de-duplicated copy."))

    # per-column
    ncols = len(header)
    columns = []
    for ci, name in enumerate(header):
        values = [(r[ci] if ci < len(r) else "") for r in body]
        profile, cf = profile_column(name, values)
        columns.append(profile)
        if profile["count"] and profile["missing_ratio"] >= missing_threshold:
            if profile["type"] == "null":
                findings.append(Finding("CSV003", "medium", name,
                                        "column is entirely empty (%d rows, 0 values)" % profile["count"],
                                        "Drop it or populate it -- an all-null column carries nothing."))
            else:
                findings.append(Finding("CSV003", "medium", name,
                                        "column is %.0f%% empty (%d of %d rows)"
                                        % (profile["missing_ratio"] * 100, profile["missing"], profile["count"]),
                                        "Investigate why the field is unpopulated before using it."))
        findings.extend(cf)

    if has_bom(path):
        findings.append(Finding("CSV012", "info", None,
                                "file starts with a UTF-8 BOM",
                                "Usually harmless; some tools treat the first header as \\ufeffname."))

    summary = {
        "rows": len(body),
        "cols": ncols,
        "delimiter": delim,
        "cells": len(body) * ncols,
        "missing_cells": sum(c["missing"] for c in columns),
        "columns": columns,
    }
    return summary, findings, header, body, delim


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
def clean(path, out_path, delimiter, has_header, dedupe):
    header, body, _ = read_table(path, delimiter, has_header, None)
    cleaned = []
    for row in body:
        new = []
        for cell in row:
            c = cell.strip()
            new.append("" if c.lower() in NULL_TOKENS else c)
        cleaned.append(new)
    if dedupe:
        seen, uniq = set(), []
        for r in cleaned:
            k = tuple(r)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        cleaned = uniq
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        if has_header:
            w.writerow([h.strip() for h in header])
        w.writerows(cleaned)
    return len(cleaned)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_markdown(summary, findings, path, top):
    counts = {s: 0 for s in SEVERITY_ORDER}
    for f in findings:
        counts[f.severity] += 1
    lines = ["# csv-audit report", "",
             "File: `%s`" % os.path.basename(path), "",
             "**%d row(s) x %d column(s)** — %d finding(s): %d high, %d medium, %d low, %d info"
             % (summary["rows"], summary["cols"], len(findings),
                counts["high"], counts["medium"], counts["low"], counts["info"]), ""]
    if summary["cells"]:
        lines.append("Missing cells: %d of %d (%.1f%%)"
                     % (summary["missing_cells"], summary["cells"],
                        100.0 * summary["missing_cells"] / summary["cells"]))
        lines.append("")

    if summary["columns"]:
        lines.append("## Column profile")
        lines.append("")
        lines.append("| # | column | type | missing | unique | notes |")
        lines.append("|---|--------|------|---------|--------|-------|")
        for i, c in enumerate(summary["columns"]):
            note = ""
            if "min" in c:
                note = "range %s..%s" % (_fmt(c["min"]), _fmt(c["max"]))
            lines.append("| %d | %s | %s | %d (%.0f%%) | %d | %s |"
                         % (i, _esc(c["name"]), c["type"], c["missing"],
                            c["missing_ratio"] * 100, c["unique"], note))
        lines.append("")

    if not findings:
        lines.append("No data-quality problems detected in the columns checked.")
        return "\n".join(lines) + "\n"

    lines.append("## Findings")
    lines.append("")
    for f in findings:
        where = (" [%s]" % f.column) if f.column else ""
        lines.append("### `%s` [%s]%s" % (f.rule, f.severity.upper(), where))
        lines.append("")
        lines.append(f.message)
        if f.hint:
            lines.append("")
            lines.append("> %s" % f.hint)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _esc(s):
    return str(s).replace("|", "\\|")


def render_json(summary, findings, path):
    return json.dumps({
        "tool": "csv-audit",
        "version": VERSION,
        "file": os.path.abspath(path),
        "summary": {**{k: v for k, v in summary.items() if k != "columns"},
                    "total_findings": len(findings),
                    **{s: sum(1 for f in findings if f.severity == s) for s in SEVERITY_ORDER}},
        "columns": summary["columns"],
        "findings": [{"rule": f.rule, "severity": f.severity, "column": f.column,
                      "message": f.message, "hint": f.hint} for f in findings],
    }, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="csv-audit",
                                description="Profile a CSV/TSV and report data-quality problems (standard library only).")
    p.add_argument("file", help="CSV/TSV file to audit")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.add_argument("--fail-on", choices=("info", "low", "medium", "high"), default=None)
    p.add_argument("--delimiter", default=None, help="field delimiter (default: sniffed)")
    p.add_argument("--no-header", action="store_true", help="treat the first row as data")
    p.add_argument("--max-rows", type=int, default=None, help="read at most N data rows")
    p.add_argument("--missing-threshold", type=float, default=0.5,
                   help="flag columns at/above this missing ratio (default: 0.5)")
    p.add_argument("--top", type=int, default=5, help="reserved (kept for compatibility)")
    p.add_argument("--clean", metavar="OUT", default=None, help="write a trimmed/normalized copy")
    p.add_argument("--dedupe", action="store_true", help="with --clean, also drop duplicate rows")
    p.add_argument("--ignore", action="append", default=[], metavar="RULE", help="ignore a rule id (repeatable)")
    p.add_argument("--version", action="version", version="csv-audit %s" % VERSION)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not os.path.isfile(args.file):
        print("csv-audit: no such file: %s" % args.file, file=sys.stderr)
        return 2

    has_header = not args.no_header
    try:
        summary, findings, _header, _body, delim = analyze(
            args.file, args.delimiter, has_header, args.max_rows, args.missing_threshold)
    except Exception as exc:
        print("csv-audit: could not read %s: %s" % (args.file, exc), file=sys.stderr)
        return 2

    ignore = {x for x in args.ignore if re.match(r"^CSV\d+$", x)}
    findings = [f for f in findings if f.rule not in ignore]
    uniq, seen = [], set()
    for f in findings:
        if f.key() in seen:
            continue
        seen.add(f.key())
        uniq.append(f)
    uniq.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.column or "", f.rule))

    out = render_markdown(summary, uniq, args.file, args.top) if args.format == "markdown" \
        else render_json(summary, uniq, args.file)
    sys.stdout.write(out)

    if args.clean:
        try:
            n = clean(args.file, args.clean, args.delimiter, has_header, args.dedupe)
            sys.stderr.write("csv-audit: wrote %d cleaned row(s) to %s\n" % (n, args.clean))
        except Exception as exc:
            print("csv-audit: could not write %s: %s" % (args.clean, exc), file=sys.stderr)
            return 2

    if args.fail_on:
        threshold = SEVERITY_ORDER[args.fail_on]
        if any(SEVERITY_ORDER[f.severity] >= threshold for f in uniq):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
