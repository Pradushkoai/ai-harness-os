"""Runner: find java + jar, execute `analyze`, return the raw report.

Verified invocation (bsl-language-server v1.0.7):
    java -jar <jar> analyze -s <srcDir> -r json -o <outDir> -q [-c <config>]
    report file: <outDir>/bsl-json.json
    exit code:  0 even when diagnostics are found (they are NOT process errors)

Discovery order:
    java: $BSL_JAVA -> $JAVA_HOME/bin/java -> `java` on PATH
    jar:  $BSL_LS_JAR -> ./bsl-language-server.jar -> ~/.bsl-language-server/bsl-language-server.jar
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Mapping, Optional, Sequence

from .types import BslVerifyError

DOWNLOAD_URL = "https://github.com/1c-syntax/bsl-language-server/releases"
JAR_CONVENTIONAL_DIR = ".bsl-language-server"
JAR_FILENAME = "bsl-language-server.jar"
REPORT_FILENAME = "bsl-json.json"

# subprocess.run substitute injected by tests:
# (cmd, timeout, capture_output, env?) -> CompletedProcess-like
Runner = Callable[..., SimpleNamespace]


class JavaNotFoundError(BslVerifyError):
    def __init__(self) -> None:
        super().__init__(
            "java not found. Install JRE/JDK 17+ or point BSL_JAVA / JAVA_HOME "
            "to the java executable."
        )


class JarNotFoundError(BslVerifyError):
    def __init__(self, searched: Sequence[str]) -> None:
        locations = "; ".join(str(s) for s in searched)
        super().__init__(
            f"bsl-language-server.jar not found (searched: {locations}). "
            f"Download the exec.jar from {DOWNLOAD_URL} and either set "
            f"BSL_LS_JAR=/path/to/bsl-language-server.jar or copy it to "
            f"~/{JAR_CONVENTIONAL_DIR}/{JAR_FILENAME}. Run `bsl-doctor` for details."
        )


class AnalyzeExecutionError(BslVerifyError):
    def __init__(self, returncode: int, stderr_tail: str) -> None:
        tail = stderr_tail.strip().splitlines()[-5:] if stderr_tail else []
        details = "\n".join(tail)
        super().__init__(
            f"bsl-language-server analyze failed (exit {returncode})"
            + (f":\n{details}" if details else "")
        )


# -- discovery -------------------------------------------------------------------


def find_java(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    env = os.environ if env is None else env

    explicit = env.get("BSL_JAVA")
    if explicit and Path(explicit).is_file():
        return explicit

    java_home = env.get("JAVA_HOME")
    if java_home:
        exe = "java.exe" if os.name == "nt" else "java"
        candidate = Path(java_home) / "bin" / exe
        if candidate.is_file():
            return str(candidate)

    which = shutil.which("java")
    return which


def find_jar(
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
) -> Optional[str]:
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else Path(cwd)

    explicit = env.get("BSL_LS_JAR")
    if explicit and Path(explicit).is_file():
        return explicit

    candidates = [
        cwd / JAR_FILENAME,
        Path.home() / JAR_CONVENTIONAL_DIR / JAR_FILENAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def jar_search_locations(env: Optional[Mapping[str, str]] = None) -> list[str]:
    env = os.environ if env is None else env
    return [
        f"${{BSL_LS_JAR}}={env.get('BSL_LS_JAR', '')}",
        str(Path.cwd() / JAR_FILENAME),
        str(Path.home() / JAR_CONVENTIONAL_DIR / JAR_FILENAME),
    ]


# -- execution ---------------------------------------------------------------------


def build_analyze_command(
    java: str,
    jar: str,
    src_dir: str,
    out_dir: str,
    config: Optional[str] = None,
) -> list[str]:
    cmd = [
        java,
        "-jar",
        jar,
        "analyze",
        "-s",
        str(src_dir),
        "-r",
        "json",
        "-o",
        str(out_dir),
        "-q",
    ]
    if config:
        cmd += ["-c", str(config)]
    return cmd


def run_analyze(
    src_dir: str,
    *,
    java: Optional[str] = None,
    jar: Optional[str] = None,
    config: Optional[str] = None,
    out_dir: Optional[str] = None,
    timeout_s: float = 120.0,
    env: Optional[Mapping[str, str]] = None,
    subprocess_run: Optional[Runner] = None,
) -> str:
    """Run `analyze` on a source directory and return the raw report JSON.

    Raises JavaNotFoundError / JarNotFoundError / AnalyzeExecutionError.
    `out_dir` defaults to a managed temp directory (always cleaned up).
    """

    java = java or find_java(env)
    if not java:
        raise JavaNotFoundError()
    jar = jar or find_jar(env)
    if not jar:
        raise JarNotFoundError(jar_search_locations(env))

    src_path = Path(src_dir)
    if not src_path.is_dir():
        raise AnalyzeExecutionError(2, f"srcDir is not a directory: {src_dir}")

    owned_out_dir = out_dir is None
    if owned_out_dir:
        out_dir = tempfile.mkdtemp(prefix="bsl-verify-")
    report_path = Path(out_dir) / REPORT_FILENAME

    cmd = build_analyze_command(java, jar, src_dir, out_dir, config)
    run = subprocess_run or subprocess.run
    try:
        completed = run(
            cmd,
            timeout=timeout_s,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise AnalyzeExecutionError(
            -1, f"timeout after {timeout_s:.0f}s while analyzing {src_dir}"
        ) from exc
    finally:
        pass  # cleanup happens below regardless of outcome

    try:
        if completed.returncode != 0:
            raise AnalyzeExecutionError(completed.returncode, completed.stderr or "")
        if not report_path.is_file():
            raise AnalyzeExecutionError(
                completed.returncode,
                f"no {REPORT_FILENAME} produced in {out_dir} "
                f"(stdout: {(completed.stdout or '').strip()[:200]})",
            )
        return report_path.read_text(encoding="utf-8", errors="replace")
    finally:
        if owned_out_dir:
            shutil.rmtree(out_dir, ignore_errors=True)


def java_version(java: Optional[str] = None) -> Optional[str]:
    """First line of `java -version` (goes to stderr on most JVMs)."""

    java = java or find_java()
    if not java:
        return None
    try:
        completed = subprocess.run(
            [java, "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        stream = completed.stderr or completed.stdout or ""
        first = stream.strip().splitlines()
        return first[0] if first else ""
    except (subprocess.SubprocessError, OSError):
        return None
