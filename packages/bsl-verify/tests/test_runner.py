"""Runner tests: discovery order, command construction, error handling.

All subprocess calls are faked — no java, no jar, no network.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bsl_verify import runner
from bsl_verify.runner import (
    AnalyzeExecutionError,
    JarNotFoundError,
    JavaNotFoundError,
    build_analyze_command,
    find_jar,
    find_java,
    run_analyze,
)
from conftest import REAL_REPORT


def fake_subprocess(report_json: str = REAL_REPORT, returncode: int = 0, commands=None):
    """Fake subprocess.run: writes the report into the -o dir from the cmd."""

    def _run(cmd, timeout=None, capture_output=False, text=False, **kw):
        if commands is not None:
            commands.append(cmd)
        out_dir = Path(cmd[cmd.index("-o") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "bsl-json.json").write_text(report_json, encoding="utf-8")
        return __import__("subprocess").CompletedProcess(
            args=cmd, returncode=returncode, stdout="", stderr=""
        )

    return _run


class TestFindJava:
    def test_bsl_java_env_wins(self, tmp_path, monkeypatch):
        java = tmp_path / "java"
        java.write_text("x")
        monkeypatch.setenv("BSL_JAVA", str(java))
        assert find_java() == str(java)

    def test_java_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BSL_JAVA", raising=False)
        home = tmp_path / "jdk"
        (home / "bin").mkdir(parents=True)
        # find_java() looks for java.exe on Windows, plain java elsewhere;
        # the fixture must follow the same rule (code is right, test was
        # written for Linux only).
        exe = home / "bin" / ("java.exe" if os.name == "nt" else "java")
        exe.write_text("x")
        monkeypatch.setenv("JAVA_HOME", str(home))
        assert find_java() == str(exe)

    def test_bsl_java_pointing_to_missing_file_falls_back(self, monkeypatch):
        monkeypatch.setenv("BSL_JAVA", "/no/such/java")
        monkeypatch.delenv("JAVA_HOME", raising=False)
        monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/java")
        assert find_java() == "/usr/bin/java"

    def test_nothing_found(self, monkeypatch):
        monkeypatch.delenv("BSL_JAVA", raising=False)
        monkeypatch.delenv("JAVA_HOME", raising=False)
        monkeypatch.setattr(runner.shutil, "which", lambda name: None)
        assert find_java() is None


class TestFindJar:
    def test_env_wins(self, tmp_path, monkeypatch):
        jar = tmp_path / "custom.jar"
        jar.write_text("x")
        monkeypatch.setenv("BSL_LS_JAR", str(jar))
        assert find_jar() == str(jar)

    def test_cwd_convention(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BSL_LS_JAR", raising=False)
        jar = tmp_path / "bsl-language-server.jar"
        jar.write_text("x")
        assert find_jar(cwd=tmp_path) == str(jar)

    def test_home_convention(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BSL_LS_JAR", raising=False)
        home = tmp_path / ".bsl-language-server"
        home.mkdir()
        jar = home / "bsl-language-server.jar"
        jar.write_text("x")
        monkeypatch.setattr(runner.Path, "home", lambda: tmp_path)
        assert find_jar(cwd=tmp_path / "empty-cwd") == str(jar)

    def test_not_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BSL_LS_JAR", raising=False)
        monkeypatch.setattr(runner.Path, "home", lambda: tmp_path)
        assert find_jar(cwd=tmp_path) is None


class TestCommandConstruction:
    def test_full_command(self):
        cmd = build_analyze_command(
            "/usr/bin/java", "/jars/bsl.jar", "/src", "/out", config="/cfg.json"
        )
        assert cmd == [
            "/usr/bin/java", "-jar", "/jars/bsl.jar", "analyze",
            "-s", "/src", "-r", "json", "-o", "/out", "-q", "-c", "/cfg.json",
        ]

    def test_without_config(self):
        cmd = build_analyze_command("java", "jar", "/src", "/out")
        assert "-c" not in cmd


class TestRunAnalyze:
    def test_happy_path_returns_report(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        out = tmp_path / "out"
        result = run_analyze(
            str(src), java="/usr/bin/java", jar="/jars/bsl.jar",
            out_dir=str(out), subprocess_run=fake_subprocess(),
        )
        assert result == REAL_REPORT
        assert (out / "bsl-json.json").exists()  # kept: caller-managed out_dir

    def test_managed_out_dir_cleaned_up(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        result = run_analyze(
            str(src), java="java", jar="jar", subprocess_run=fake_subprocess(),
        )
        assert result == REAL_REPORT
        leftovers = list(tmp_path.glob("bsl-verify-*"))
        assert leftovers == []  # our temp dirs are cleaned

    def test_no_java_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BSL_JAVA", raising=False)
        monkeypatch.delenv("JAVA_HOME", raising=False)
        monkeypatch.setattr(runner.shutil, "which", lambda name: None)
        with pytest.raises(JavaNotFoundError):
            run_analyze(str(tmp_path))

    def test_no_jar_raises_with_hint(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BSL_LS_JAR", raising=False)
        monkeypatch.setattr(runner.Path, "home", lambda: tmp_path)
        with pytest.raises(JarNotFoundError) as excinfo:
            run_analyze(str(tmp_path), java="/usr/bin/java")
        assert "releases" in str(excinfo.value)  # download URL in the message

    def test_nonzero_exit_raises(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        with pytest.raises(AnalyzeExecutionError) as excinfo:
            run_analyze(
                str(src), java="java", jar="jar",
                subprocess_run=fake_subprocess(returncode=1),
            )
        assert "exit 1" in str(excinfo.value)

    def test_missing_src_dir_raises(self, tmp_path):
        with pytest.raises(AnalyzeExecutionError):
            run_analyze(str(tmp_path / "nope"), java="java", jar="jar",
                        subprocess_run=fake_subprocess())

    def test_no_report_file_raises(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()

        def run_without_report(cmd, **kw):
            import subprocess

            return subprocess.CompletedProcess(cmd, 0, "", "")

        with pytest.raises(AnalyzeExecutionError) as excinfo:
            run_analyze(str(src), java="java", jar="jar",
                        subprocess_run=run_without_report)
        assert "bsl-json.json" in str(excinfo.value)

    def test_timeout_raises(self, tmp_path):
        import subprocess

        src = tmp_path / "src"
        src.mkdir()

        def slow(cmd, timeout=None, **kw):
            raise subprocess.TimeoutExpired(cmd, timeout)

        with pytest.raises(AnalyzeExecutionError) as excinfo:
            run_analyze(str(src), java="java", jar="jar", timeout_s=5,
                        subprocess_run=slow)
        assert "timeout" in str(excinfo.value)
