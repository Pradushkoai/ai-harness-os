"""INTEGRATION tests — run the real bsl-language-server (java + jar required).

Run explicitly:
    BSL_LS_JAR=/path/to/bsl-language-server.jar pytest -m integration -v

Skipped otherwise (no network, no java burn in unit CI).
"""

from __future__ import annotations

import os
import shutil

import pytest

from bsl_verify.policy import LS_FALSE_POSITIVE_CODES, VerifyPolicy
from bsl_verify.types import Severity
from bsl_verify.verifier import BslVerifier
from conftest import MODULE_BROKEN, MODULE_OK

pytestmark = pytest.mark.integration


def jar_available() -> bool:
    if os.environ.get("BSL_LS_JAR"):
        return True
    return shutil.which("java") is not None and (
        (os.path.exists("bsl-language-server.jar"))
        or os.path.exists(os.path.expanduser("~/.bsl-language-server/bsl-language-server.jar"))
    )


@pytest.fixture(scope="module")
def verifier() -> BslVerifier:
    if not jar_available():
        pytest.skip("bsl-language-server.jar not available (set BSL_LS_JAR)")
    return BslVerifier(timeout_s=300.0)


class TestRealAnalysis:
    def test_broken_module_fails_policy_with_parse_error(self, verifier):
        result = verifier.verify_module_text(MODULE_BROKEN)
        assert result.passed is False
        assert result.errors >= 1
        codes = {d.code for report in result.files for d in report.diagnostics}
        assert "ParseError" in codes
        assert result.duration_ms > 0

    def test_warning_only_module_passes_default_policy(self, verifier):
        result = verifier.verify_module_text(MODULE_OK)
        # module_ok triggers at most Information/Warning diagnostics (DeprecatedMessage)
        assert result.errors == 0
        assert result.passed is True
        assert len(result.files) == 1

    def test_metrics_present(self, verifier):
        result = verifier.verify_module_text(MODULE_OK)
        metrics = result.files[0].metrics
        assert metrics is not None
        assert metrics.lines == 5
        assert metrics.procedures == 1
        assert metrics.functions == 0

    def test_ignore_code_softens_policy(self, verifier):
        strict = BslVerifier(
            timeout_s=300.0, policy=VerifyPolicy(ignore_codes=frozenset({"ParseError"}))
        )
        result = strict.verify_module_text(MODULE_BROKEN)
        assert result.errors == 0 or all(
            d.code != "ParseError"
            for report in result.files
            for d in report.diagnostics
        )
        assert result.passed is True

    def test_verify_files_maps_path_back(self, verifier, tmp_path):
        module = tmp_path / "my_module.bsl"
        module.write_text(MODULE_BROKEN, encoding="utf-8")
        result = verifier.verify_files([str(module)])
        assert result.files
        assert result.files[0].path == str(module)
        assert result.passed is False

    def test_severity_enum_matches_real_output(self, verifier):
        result = verifier.verify_module_text(MODULE_BROKEN)
        severities = {d.severity for report in result.files for d in report.diagnostics}
        assert severities  # non-empty
        assert all(isinstance(s, Severity) for s in severities)
        assert Severity.ERROR in severities


class TestLSFalsePositives:
    """The UT 11 pilot case: em-dash in comments — ERROR for LS, fine for engines."""

    EMDASH_MODULE = (
        "// Расчёт остатков — реализация УТ 11 (em-dash U+2014 в комментарии)\n"
        "// Период — квартал, склад — основной.\n"
        "Функция Сумма(А, Б)\n"
        "    Возврат А + Б;\n"
        "КонецФункции\n"
    )

    def test_raw_policy_fails_on_em_dash(self, verifier):
        raw = BslVerifier(policy=VerifyPolicy(), timeout_s=300.0)
        result = raw.verify_module_text(self.EMDASH_MODULE)
        assert result.passed is False
        codes = [d.code for report in result.files for d in report.diagnostics]
        assert codes.count("InvalidCharacterInFile") >= 1

    def test_false_positive_policy_passes_on_em_dash(self, verifier):
        fixed = BslVerifier(
            policy=VerifyPolicy(ignore_codes=LS_FALSE_POSITIVE_CODES),
            timeout_s=300.0,
        )
        result = fixed.verify_module_text(self.EMDASH_MODULE)
        assert result.passed is True
        assert result.errors == 0

    def test_false_positive_policy_still_catches_parse_error(self, verifier):
        fixed = BslVerifier(
            policy=VerifyPolicy(ignore_codes=LS_FALSE_POSITIVE_CODES),
            timeout_s=300.0,
        )
        result = fixed.verify_module_text(MODULE_BROKEN)
        assert result.passed is False
