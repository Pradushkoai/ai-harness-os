"""Parser tests against the REAL bsl-language-server v1.0.7 output."""

from __future__ import annotations

import json
import os

import pytest

from bsl_verify.parser import ReportParseError, parse_report, uri_to_path
from bsl_verify.types import Severity
from conftest import REAL_REPORT


class TestRealReport:
    def test_parses_without_error(self):
        report = parse_report(REAL_REPORT)
        assert len(report.files) == 1

    def test_file_path_decoded_from_uri(self):
        report = parse_report(REAL_REPORT)
        path = report.files[0].path
        assert path.startswith("/")
        assert not path.startswith("file://")
        assert path.endswith("sample_broken.bsl")

    def test_diagnostics_present_and_sorted(self):
        report = parse_report(REAL_REPORT)
        diagnostics = report.files[0].diagnostics
        assert len(diagnostics) == 2
        lines = [d.range.start.line for d in diagnostics]
        assert lines == sorted(lines)

    def test_severities_pascal_case(self):
        report = parse_report(REAL_REPORT)
        by_code = {d.code: d for d in report.files[0].diagnostics}
        assert by_code["ParseError"].severity == Severity.ERROR
        assert by_code["EmptyCodeBlock"].severity == Severity.WARNING

    def test_ranges_lsp_zero_based(self):
        report = parse_report(REAL_REPORT)
        parse_error = next(
            d for d in report.files[0].diagnostics if d.code == "ParseError"
        )
        assert parse_error.range.start.line == 3  # real position from the report
        assert parse_error.range.start.character == 0

    def test_message_cyrillic_preserved(self):
        report = parse_report(REAL_REPORT)
        parse_error = next(
            d for d in report.files[0].diagnostics if d.code == "ParseError"
        )
        assert "Ошибка" in parse_error.message

    def test_metrics_parsed(self):
        report = parse_report(REAL_REPORT)
        metrics = report.files[0].metrics
        assert metrics is not None
        assert metrics.procedures == 1
        assert metrics.lines == 5
        assert metrics.ncloc == 3
        assert metrics.cyclomatic_complexity == 2

    def test_report_metadata(self):
        report = parse_report(REAL_REPORT)
        assert report.date  # ISO-ish date string
        assert "sourceDir" not in REAL_REPORT or isinstance(report.source_dir, str)


class TestTolerance:
    def test_empty_fileinfos(self):
        report = parse_report('{"date":"x","fileinfos":[],"sourceDir":"/a"}')
        assert report.files == []
        assert report.source_dir == "/a"

    def test_none_input(self):
        assert parse_report(None).files == []

    def test_dict_input_passthrough(self):
        data = json.loads(REAL_REPORT)
        assert parse_report(data).files == parse_report(REAL_REPORT).files

    def test_missing_fields(self):
        minimal = '{"fileinfos":[{"path":"file:///x/m.bsl","diagnostics":[{}]}]}'
        report = parse_report(minimal)
        diagnostic = report.files[0].diagnostics[0]
        assert diagnostic.severity == Severity.INFORMATION
        assert diagnostic.code == ""
        assert diagnostic.message == ""

    def test_severity_variants(self):
        for raw, expected in [
            ("ERROR", Severity.ERROR), ("error", Severity.ERROR),
            ("Warning", Severity.WARNING), ("warning", Severity.WARNING),
            ("INFORMATION", Severity.INFORMATION), ("Information", Severity.INFORMATION),
            ("hint", Severity.HINT), ("Hint", Severity.HINT),
            (1, Severity.ERROR), (2, Severity.WARNING),
            (3, Severity.INFORMATION), (4, Severity.HINT),
            ("WhatIsThis", Severity.INFORMATION), (None, Severity.INFORMATION),
        ]:
            data = json.dumps({
                "fileinfos": [{"path": "m.bsl",
                               "diagnostics": [{"severity": raw, "code": "X"}]}]
            })
            diagnostic = parse_report(data).files[0].diagnostics[0]
            assert diagnostic.severity == expected, f"severity {raw!r}"

    def test_non_uri_path_kept_as_is(self):
        data = '{"fileinfos":[{"path":"D:/src/module.bsl","diagnostics":[]}]}'
        assert parse_report(data).files[0].path == "D:/src/module.bsl"

    def test_invalid_json_raises(self):
        with pytest.raises(ReportParseError):
            parse_report("{not json")

    def test_wrong_root_type_raises(self):
        with pytest.raises(ReportParseError):
            parse_report("[1,2,3]")

    def test_garbage_entries_skipped(self):
        data = '{"fileinfos":["nonsense", {"path":"a.bsl","diagnostics":[]}, 42]}'
        report = parse_report(data)
        assert len(report.files) == 1


class TestUriToPath:
    def test_posix(self):
        assert uri_to_path("file:///home/x/m.bsl") == "/home/x/m.bsl"

    def test_windows_drive(self):
        assert uri_to_path("file:///D:/work/m.bsl").endswith(("D:\\work\\m.bsl", "D:/work/m.bsl"))

    def test_percent_encoded(self):
        assert uri_to_path("file:///home/%D0%BC/m.bsl") == os.path.normpath("/home/м/m.bsl")

    def test_not_a_uri(self):
        assert uri_to_path("/plain/path.bsl") == os.path.normpath("/plain/path.bsl")

    def test_empty(self):
        assert uri_to_path("") == ""

    def test_dot_segments_normalized(self):
        # REAL case from bsl-language-server v1.0.7: srcDir outside cwd
        # is reported as a path relative to the process working directory.
        uri = "file:///home/z/x/pkg/../../../../../../tmp/m.bsl"
        assert uri_to_path(uri) == os.path.normpath("/tmp/m.bsl")

    def test_dot_segments_in_report_parsed(self):
        data = json.dumps({
            "fileinfos": [{
                "path": "file:///home/z/x/pkg/../../../../../tmp/stage-1/m.bsl",
                "diagnostics": [],
            }]
        })
        assert parse_report(data).files[0].path == os.path.normpath("/tmp/stage-1/m.bsl")
