"""Extraction of BSL code from an LLM response.

Models are asked to return code in a ```bsl fence (see prompt.py), but the
extractor must survive everything else: no fence at all, a wrong language
tag, prose around the code, several fences (draft + final). Strategy:

    1. fenced blocks tagged BSL-ish (bsl / os / 1c ...) or untagged;
    2. else: any fenced block whose CONTENT looks like BSL (wrong tag case);
    3. else: the whole response, if it looks like BSL;
    4. else: None — the loop treats it as a failed iteration and tells
       the model how to answer properly.

"The last block wins": when several fences are present, models put the
final version last (draft first, corrected code after).
"""

from __future__ import annotations

import re
from typing import Optional

_FENCE_RE = re.compile(r"```[ \t]*([\w.+#-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)

_BSL_LANG_TAGS = {"", "bsl", "os", "1c", "bso", "bsl1c", "1c-bsl"}

# A text "looks like BSL" when some non-empty line starts like module code:
# a directive annotation, a preprocessor directive, or a top-level declaration.
_LOOKS_LIKE_BSL_RE = re.compile(
    r"^[ \t]*(?:&(?:Перед|После|Вместо|ИзменениеИКонтроль)"
    r"|#(?:Если|Область|Объявить|КонецОбласти|ЕслиСервер)"
    r"|(?:Процедура|Функция|Перем)\s)",
    re.MULTILINE,
)


def extract_bsl_code(text: Optional[str]) -> Optional[str]:
    """Pull BSL module code out of an LLM response; None if not found."""

    if not text or not text.strip():
        return None

    bsl_blocks: list[str] = []
    other_blocks: list[str] = []
    for match in _FENCE_RE.finditer(text):
        lang = match.group(1).strip().lower()
        content = match.group(2)
        if not content.strip():
            continue  # empty fences are noise
        (bsl_blocks if lang in _BSL_LANG_TAGS else other_blocks).append(content)

    if bsl_blocks:
        return _normalize(bsl_blocks[-1])
    for content in reversed(other_blocks):
        if _LOOKS_LIKE_BSL_RE.search(content):
            return _normalize(content)

    candidate = text.strip()
    if _LOOKS_LIKE_BSL_RE.search(candidate):
        return _normalize(candidate)
    return None


def _normalize(code: str) -> str:
    """Deterministic shape: no surrounding blank lines, exactly one trailing \\n."""

    stripped = code.strip("\r\n \t")
    return stripped + "\n" if stripped else ""
