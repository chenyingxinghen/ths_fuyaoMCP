from __future__ import annotations

import html
import json
import re


_COMMON_MARKDOWN_ESCAPES = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>])")
_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def decode_markdown_output(value: str) -> str:
    """Normalize escaped Markdown answers before CLI/Web presentation."""
    text = str(value or "")
    text = _decode_json_string(text)
    text = html.unescape(text)
    text = _decode_line_escapes(text)
    text = _strip_single_markdown_fence(text)
    text = _decode_non_code_escapes(text)
    return text


def _decode_json_string(text: str) -> str:
    stripped = text.strip()
    if len(stripped) < 2 or stripped[0] not in {"'", '"'} or stripped[-1] != stripped[0]:
        return text

    if stripped[0] == '"':
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return text
        return decoded if isinstance(decoded, str) else text

    inner = stripped[1:-1]
    return inner.replace("\\'", "'")


def _decode_common_escapes(text: str) -> str:
    if "\\t" in text:
        text = text.replace("\\t", "\t")
    if "\\u" in text:
        text = _UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)
    return _COMMON_MARKDOWN_ESCAPES.sub(r"\1", text)


def _decode_line_escapes(text: str) -> str:
    if "\\r\\n" in text or "\\n\\n" in text:
        return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    return text


def _decode_non_code_escapes(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _CODE_FENCE.finditer(text):
        parts.append(_decode_common_escapes(text[cursor:match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_decode_common_escapes(text[cursor:]))
    return "".join(parts)


def _strip_single_markdown_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*\n(?P<body>[\s\S]*?)\n```", stripped, re.IGNORECASE)
    return match.group("body") if match else text
