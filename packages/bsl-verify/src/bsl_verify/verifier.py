"""BslVerifier — the facade the future harness talks to.

Three entry points, three use cases:

    verify_dir(path)        whole-project check (in place, real paths)
    verify_files([paths])   incremental check of exact files (staged copies)
    verify_module_text(t)   the agent-loop sensor: check generated BSL code

Staging: bsl-language-server analyzes a DIRECTORY. To check exact files
without dragging in their neighbours, copies are staged into a temp dir
(basenames preserved, collisions get a numeric prefix), analyzed, and the
report paths are mapped back to the originals.

This class is the L0/L1 backpressure port. The harness loop calls
`verify_module_text` after every agent edit — parse errors and diagnostics
come back in ~seconds as structured feedback.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from .parser import RawReport, parse_report
from .policy import VerifyPolicy
from .runner import run_analyze
from .types import FileReport, VerifyResult

SUPPORTED_EXTENSIONS = {".bsl", ".os"}


class BslVerifier:
    """Runs bsl-language-server analysis and applies a verification policy."""

    def __init__(
        self,
        *,
        java: Optional[str] = None,
        jar: Optional[str] = None,
        config: Optional[str] = None,
        timeout_s: float = 120.0,
        policy: Optional[VerifyPolicy] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._java = java
        self._jar = jar
        self._config = config
        self._timeout_s = timeout_s
        self._policy = policy or VerifyPolicy()
        self._env = env

    @property
    def policy(self) -> VerifyPolicy:
        return self._policy

    # -- public API ---------------------------------------------------------

    def verify_dir(self, path: str) -> VerifyResult:
        """Analyze a directory in place (whole-project check)."""

        src_dir = str(Path(path).resolve())
        return self._run(src_dir, report_path_map=None)

    def verify_files(self, paths: Sequence[str]) -> VerifyResult:
        """Analyze exact files: staged copies, report paths mapped back."""

        if not paths:
            raise ValueError("verify_files: no paths given")
        resolved = [Path(p).resolve() for p in paths]
        missing = [str(p) for p in resolved if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"file(s) not found: {', '.join(missing)}")

        staging = Path(tempfile.mkdtemp(prefix="bsl-verify-stage-"))
        path_map: dict[str, str] = {}  # staged absolute path -> original path
        try:
            used_names: set[str] = set()
            for original in resolved:
                staged_name = original.name
                if staged_name in used_names:  # basename collision -> prefix
                    index = 1
                    while f"{index:04d}_{original.name}" in used_names:
                        index += 1
                    staged_name = f"{index:04d}_{original.name}"
                used_names.add(staged_name)
                staged_path = staging / staged_name
                shutil.copyfile(original, staged_path)
                path_map[str(staged_path)] = str(original)
            return self._run(str(staging), report_path_map=path_map)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def verify_module_text(self, text: str, *, filename: str = "module.bsl") -> VerifyResult:
        """The agent-loop sensor: verify BSL code that is not on disk yet."""

        staging = Path(tempfile.mkdtemp(prefix="bsl-verify-text-"))
        try:
            module_path = staging / filename
            module_path.write_text(text, encoding="utf-8")
            return self._run(str(staging), report_path_map=None)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _apply_filters(self, files: list) -> list:
        """Drop diagnostics excluded by the policy (ignore_codes / only_codes)
        from the reports entirely — counts, summary lines and JSON stay
        consistent with each other. Files (and their metrics) are kept."""

        if not self._policy.ignore_codes and self._policy.only_codes is None:
            return files
        return [
            FileReport(
                path=report.path,
                diagnostics=[
                    d for d in report.diagnostics if self._policy._counts(d.code)
                ],
                metrics=report.metrics,
            )
            for report in files
        ]

    # -- internals -----------------------------------------------------------

    def _run(
        self,
        src_dir: str,
        report_path_map: Optional[dict[str, str]] = None,
    ) -> VerifyResult:
        started = time.monotonic()
        raw_json = run_analyze(
            src_dir,
            java=self._java,
            jar=self._jar,
            config=self._config,
            timeout_s=self._timeout_s,
            env=self._env,
        )
        duration_ms = (time.monotonic() - started) * 1000.0

        raw: RawReport = parse_report(raw_json)
        if report_path_map:
            _remap_report_paths(raw, report_path_map)

        filtered = self._apply_filters(raw.files)
        verdict = self._policy.evaluate(filtered)
        return VerifyResult(
            files=filtered,
            errors=verdict.errors,
            warnings=verdict.warnings,
            informations=verdict.informations,
            hints=verdict.hints,
            passed=verdict.passed,
            violations=list(verdict.violations),
            duration_ms=duration_ms,
            report_date=raw.date,
            source_dir=raw.source_dir,
            policy=self._policy.describe(),
        )


def _remap_report_paths(raw: RawReport, path_map: dict[str, str]) -> None:
    """Point staged report paths back at the original files."""

    for report in raw.files:
        original = path_map.get(report.path)
        if original is not None:
            report.path = original


def collect_bsl_files(paths: Iterable[str]) -> list[Path]:
    """Expand user paths into a flat list of supported module files."""

    collected: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            collected.extend(
                p for p in sorted(path.rglob("*")) if p.suffix.lower() in SUPPORTED_EXTENSIONS
            )
        elif path.is_file():
            collected.append(path)
    return collected
