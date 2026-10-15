#!/usr/bin/env python3
"""Tests for csv-audit. Run: python3 -m unittest discover -s tests -v"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "csv_audit.py")

sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import csv_audit as ca  # noqa: E402


class Temp(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def csv(self, content, name="data.csv"):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return path

    def rules(self, content, **kw):
        path = self.csv(content)
        _summary, findings, _h, _b, _d = ca.analyze(path, **kw)
        return {f.rule for f in findings}


class Types(Temp):
    def test_classify(self):
        self.assertEqual(ca.classify("42"), "int")
        self.assertEqual(ca.classify("3.14"), "float")
        self.assertEqual(ca.classify("true"), "bool")
        self.assertEqual(ca.classify("2020-01-02"), "date")
        self.assertEqual(ca.classify("hello"), "string")
        self.assertEqual(ca.classify("N/A"), "null")
        self.assertEqual(ca.classify(""), "null")

    def test_percentile(self):
        self.assertEqual(ca._percentile([1, 2, 3, 4], 50), 2.5)


class Rules(Temp):
    def test_csv001_duplicate_header(self):
        self.assertIn("CSV001", self.rules("a,a\n1,2\n"))

    def test_csv002_constant_column(self):
        self.assertIn("CSV002", self.rules("a,b\n1,x\n1,x\n1,x\n"))

    def test_csv003_high_missing(self):
        rows = ["a,b"] + ["1," for _ in range(4)]
        self.assertIn("CSV003", self.rules("\n".join(rows) + "\n"))

    def test_csv004_duplicate_rows(self):
        self.assertIn("CSV004", self.rules("a,b\n1,2\n1,2\n"))

    def test_csv005_mixed_types(self):
        self.assertIn("CSV005", self.rules("v\n10\n20\nabc\n"))

    def test_csv006_inconsistent_nulls(self):
        self.assertIn("CSV006", self.rules("v\n1\nN/A\nNULL\n"))

    def test_csv007_outliers(self):
        rows = ["v"] + [str(x) for x in [1, 2, 3, 4, 5, 100]]
        self.assertIn("CSV007", self.rules("\n".join(rows) + "\n"))

    def test_csv008_whitespace(self):
        self.assertIn("CSV008", self.rules("v\n ok \n x \n"))

    def test_csv009_date_formats(self):
        self.assertIn("CSV009", self.rules("d\n2020-01-02\n03/04/2020\n"))

    def test_csv010_high_cardinality(self):
        rows = ["id"] + ["item%d" % i for i in range(25)]
        self.assertIn("CSV010", self.rules("\n".join(rows) + "\n"))

    def test_csv011_ragged_rows(self):
        self.assertIn("CSV011", self.rules("a,b,c\n1,2\n"))

    def test_csv013_case_dupes(self):
        self.assertIn("CSV013", self.rules("c\nUSA\nusa\nUK\nuK\n"))

    def test_csv012_bom(self):
        path = os.path.join(self.root, "bom.csv")
        with open(path, "wb") as fh:
            fh.write(b"\xef\xbb\xbfa,b\n1,2\n")
        _s, findings, _h, _b, _d = ca.analyze(path)
        self.assertIn("CSV012", {f.rule for f in findings})

    def test_clean_table_no_findings(self):
        content = "a,b\n1,2\n3,4\n"
        self.assertEqual(self.rules(content), set())

    def test_blank_line_is_not_ragged(self):
        # a blank line (or an extra trailing newline) is not a malformed row
        self.assertNotIn("CSV011", self.rules("a,b\n1,2\n3,4\n\n"))

    def test_empty_file_summary_is_renderable(self):
        path = os.path.join(self.root, "empty.csv")
        open(path, "w").close()
        summary, findings, _h, _b, _d = ca.analyze(path)
        self.assertIn("cells", summary)
        self.assertIn("CSV000", {f.rule for f in findings})

    def test_tsv_sniff(self):
        path = self.csv("a\tb\n1\t2\n", name="data.tsv")
        summary, _f, _h, _b, delim = ca.analyze(path)
        self.assertEqual(delim, "\t")
        self.assertEqual(summary["cols"], 2)

    def test_no_header_mode(self):
        path = self.csv("1,2\n3,4\n")
        summary, _f, _h, _b, _d = ca.analyze(path, has_header=False)
        self.assertEqual(summary["rows"], 2)


class Clean(Temp):
    def test_clean_trims_and_normalizes_nulls(self):
        path = self.csv("a,b\n 1 , N/A\n2,x\n")
        out = os.path.join(self.root, "clean.csv")
        n = ca.clean(path, out, None, True, dedupe=False)
        self.assertEqual(n, 2)
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("1,", text)
        self.assertNotIn("N/A", text)
        self.assertNotIn(" ", text.replace("\n", ""))

    def test_clean_dedupe(self):
        path = self.csv("a,b\n1,2\n1,2\n3,4\n")
        out = os.path.join(self.root, "clean.csv")
        n = ca.clean(path, out, None, True, dedupe=True)
        self.assertEqual(n, 2)

    def test_clean_uses_lf_not_crlf(self):
        path = self.csv("a,b\n1,2\n")
        out = os.path.join(self.root, "clean.csv")
        ca.clean(path, out, None, True, dedupe=False)
        with open(out, "rb") as fh:
            self.assertNotIn(b"\r", fh.read())


class Cli(Temp):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_json_format(self):
        path = self.csv("a,a\n1,2\n")
        proc = self.run_cli(path, "--format", "json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["tool"], "csv-audit")
        self.assertGreaterEqual(len(data["findings"]), 1)

    def test_fail_on_medium(self):
        path = self.csv("a,b,c\n1,2\n")
        proc = self.run_cli(path, "--fail-on", "medium")
        self.assertEqual(proc.returncode, 1)

    def test_clean_exit_zero(self):
        path = self.csv("a,b\n1,2\n3,4\n")
        proc = self.run_cli(path, "--fail-on", "info")
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_ignore_rule(self):
        path = self.csv("a,a\n1,2\n")
        proc = self.run_cli(path, "--format", "json", "--ignore", "CSV001")
        data = json.loads(proc.stdout)
        self.assertNotIn("CSV001", {f["rule"] for f in data["findings"]})

    def test_clean_flag_writes_file(self):
        path = self.csv("a,b\n 1 ,x\n")
        out = os.path.join(self.root, "out.csv")
        proc = self.run_cli(path, "--clean", out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.exists(out))

    def test_missing_file_exit_2(self):
        proc = self.run_cli(os.path.join(self.root, "nope.csv"))
        self.assertEqual(proc.returncode, 2)

    def test_empty_file_markdown_no_crash(self):
        path = os.path.join(self.root, "empty.csv")
        open(path, "w").close()
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CSV000", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
