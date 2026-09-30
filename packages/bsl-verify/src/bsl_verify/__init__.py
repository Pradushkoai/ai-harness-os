"""bsl-verify: 1C/BSL static verification adapter over bsl-language-server.

This is the L0/L1 backpressure sensor of the future 1C harness:
the agent writes or edits BSL code -> bsl-verify runs bsl-language-server
`s analyze` (parse errors = L0, 250+ diagnostics = L1) -> the agent gets
structured feedback in ~seconds, before any expensive semantic checks.

Contract with the outside world (verified against bsl-language-server
v1.0.7, `analyze -r json`):

    command:  java -jar <jar> analyze -s <srcDir> -r json -o <outDir> -q [-c cfg]
    report:   <outDir>/bsl-json.json
    root:     {"date": "...", "fileinfos": [...], "sourceDir": "..."}
    fileinfo: {"path": "file:///abs/path.bsl", "diagnostics": [...], "metrics": {...}}
    severity: "Error" | "Warning" | "Information" | "Hint"  (PascalCase)
    lines:    0-based (LSP convention); humans get 1-based in summaries

Pure stdlib. No network. No secrets.
"""

from .policy import VerifyPolicy
from .types import (
    BslVerifyError,
    Diagnostic,
    FileMetrics,
    FileReport,
    Position,
    Range,
    Severity,
    VerifyResult,
)
from .verifier import BslVerifier

__version__ = "0.1.1"

__all__ = [
    "BslVerifyError",
    "BslVerifier",
    "Diagnostic",
    "FileMetrics",
    "FileReport",
    "Position",
    "Range",
    "Severity",
    "VerifyPolicy",
    "VerifyResult",
    "__version__",
]
