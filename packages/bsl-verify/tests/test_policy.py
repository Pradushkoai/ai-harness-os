"""Policy tests: thresholds, ignore/only codes."""

from __future__ import annotations

from bsl_verify.parser import parse_report
from bsl_verify.policy import VerifyPolicy
from bsl_verify.types import Diagnostic, FileReport, Position, Range, Severity
from conftest import REAL_REPORT


def report_with(*severities_and_codes):
    diagnostics = [
        Diagnostic(
            range=Range(start=Position(i, 0), end=Position(i, 1)),
            severity=severity,
            code=code,
            message="m",
        )
        for i, (severity, code) in enumerate(severities_and_codes)
    ]
    return FileReport(path="m.bsl", diagnostics=diagnostics)


class TestDefaultPolicy:
    def test_any_error_fails(self):
        verdict = VerifyPolicy().evaluate(
            [report_with((Severity.ERROR, "ParseError"))]
        )
        assert verdict.passed is False
        assert verdict.errors == 1

    def test_warnings_do_not_fail(self):
        verdict = VerifyPolicy().evaluate(
            [report_with((Severity.WARNING, "W"))]
        )
        assert verdict.passed is True
        assert verdict.warnings == 1

    def test_clean_report_passes(self):
        verdict = VerifyPolicy().evaluate([report_with()])
        assert verdict.passed is True
        assert verdict.errors == 0


class TestThresholds:
    def test_max_errors_zero_blocks_single_error(self):
        policy = VerifyPolicy(max_errors=0)
        assert policy.evaluate([report_with((Severity.ERROR, "E"))]).passed is False

    def test_max_errors_one_allows_single_error(self):
        policy = VerifyPolicy(max_errors=1)
        verdict = policy.evaluate([report_with((Severity.ERROR, "E"))])
        assert verdict.passed is True

    def test_max_warnings_off_by_default(self):
        policy = VerifyPolicy()
        many = [report_with(*[(Severity.WARNING, "W")] * 10)]
        assert policy.evaluate(many).passed is True

    def test_max_warnings_active_fails(self):
        policy = VerifyPolicy(max_warnings=2)
        many = [report_with(*[(Severity.WARNING, "W")] * 3)]
        verdict = policy.evaluate(many)
        assert verdict.passed is False
        assert any("warnings" in v for v in verdict.violations)


class TestCodeFilters:
    def test_ignore_codes_excluded_from_counts(self):
        policy = VerifyPolicy(ignore_codes=frozenset({"ParseError"}))
        verdict = policy.evaluate(
            [report_with((Severity.ERROR, "ParseError"), (Severity.ERROR, "OtherCode"))]
        )
        assert verdict.errors == 1
        assert verdict.passed is False

    def test_ignore_codes_can_make_it_pass(self):
        policy = VerifyPolicy(ignore_codes=frozenset({"ParseError"}))
        verdict = policy.evaluate([report_with((Severity.ERROR, "ParseError"))])
        assert verdict.passed is True

    def test_only_codes_whitelist(self):
        policy = VerifyPolicy(only_codes=frozenset({"MyCode"}))
        verdict = policy.evaluate(
            [report_with((Severity.ERROR, "ParseError"), (Severity.WARNING, "MyCode"))]
        )
        assert verdict.errors == 0
        assert verdict.warnings == 1

    def test_real_report_default_policy_fails(self):
        report = parse_report(REAL_REPORT)
        verdict = VerifyPolicy().evaluate(report.files)
        assert verdict.passed is False  # ParseError is an Error
        assert verdict.errors == 1
        assert verdict.warnings == 1


class TestDescribe:
    def test_describe_default(self):
        text = VerifyPolicy().describe()
        assert "max_errors=0" in text
        assert "max_warnings=off" in text

    def test_describe_with_filters(self):
        text = VerifyPolicy(
            ignore_codes=frozenset({"A", "B"}), only_codes=None
        ).describe()
        assert "ignore=A,B" in text
