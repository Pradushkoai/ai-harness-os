"""Shared fixtures: real captured analyze output + fake subprocess runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Real output of bsl-language-server v1.0.7 `analyze -r json` (captured 2026-09-30).
REAL_REPORT = (FIXTURES / "sample_report.json").read_text(encoding="utf-8")

MODULE_BROKEN = (FIXTURES / "module_broken.bsl").read_text(encoding="utf-8")
MODULE_OK = (FIXTURES / "module_ok.bsl").read_text(encoding="utf-8")


def make_fake_run_analyze(
    report_json: str = REAL_REPORT,
    *,
    report_filename="bsl-json.json",
    commands: list | None = None,
    returncode: int = 0,
):
    """Fake runner usable as verifier.run_analyze substitute.

    Mirrors the real contract: with no out_dir given, a temp dir is created
    and cleaned up (never writes into the project tree). Writing the report
    into the -o directory also asserts the command is shaped correctly.
    """

    import shutil
    import tempfile

    captured = commands if commands is not None else []

    def fake_run_analyze(src_dir, *, java=None, jar=None, config=None,
                         out_dir=None, timeout_s=120.0, env=None,
                         subprocess_run=None):
        owned = out_dir is None
        out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="bsl-test-"))
        try:
            cmd = ["java", "-jar", "bsl-language-server.jar", "analyze",
                   "-s", str(src_dir), "-r", "json", "-o", str(out), "-q"]
            if config:
                cmd += ["-c", str(config)]
            captured.append(cmd)
            (out / report_filename).write_text(report_json, encoding="utf-8")
        finally:
            if owned:
                shutil.rmtree(out, ignore_errors=True)
        return report_json

    return fake_run_analyze


@pytest.fixture
def real_report_dict():
    return json.loads(REAL_REPORT)


__all__ = [
    "FIXTURES",
    "MODULE_BROKEN",
    "MODULE_OK",
    "REAL_REPORT",
    "make_fake_run_analyze",
]
