from __future__ import annotations

from pathlib import Path


_KNOWLEDGE_PATH = Path(__file__).resolve().parents[2] / "docs" / "knowledge" / "open_source_quant_ai.md"


def load_quant_knowledge() -> str:
    if not _KNOWLEDGE_PATH.exists():
        return ""
    return _KNOWLEDGE_PATH.read_text(encoding="utf-8").strip()


def quant_knowledge_injection() -> str:
    knowledge = load_quant_knowledge()
    if not knowledge:
        return ""
    return (
        "External quant AI knowledge base distilled from Qlib, FinRL, FinGPT, "
        "and RD-Agent. Treat it as methodological guidance, not market data.\n\n"
        f"{knowledge}"
    )

