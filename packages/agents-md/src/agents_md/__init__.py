"""agents-md: AGENTS.md generator for AI coding agents.

Analyzes a project directory (python / js-ts / 1C:EDT / 1C:Configurator
dump / generic), builds a Russian-language AGENTS.md following the
agents.md conventions (root file, concise sections, hard size limit),
and validates existing files against the basics of the standard.

Pure stdlib: no template engine, no network, no secrets. The generated
file is a starting draft — the human reviews, edits and commits it.

CLI:
    agents-md init [path] [--force] [--stdout]
    agents-md validate [path] [--file AGENTS.md]
"""

from ._version import __version__
from .detect import KIND_1C_EDT, KIND_1C_XML, KIND_GENERIC, KIND_JS_TS, KIND_PYTHON, detect_project
from .generate import generate_agents_md
from .structure import scan_structure
from .types import ProjectInfo, StructureEntry
from .validate import validate_agents_md

__all__ = [
    "KIND_1C_EDT",
    "KIND_1C_XML",
    "KIND_GENERIC",
    "KIND_JS_TS",
    "KIND_PYTHON",
    "ProjectInfo",
    "StructureEntry",
    "detect_project",
    "generate_agents_md",
    "scan_structure",
    "validate_agents_md",
    "__version__",
]
