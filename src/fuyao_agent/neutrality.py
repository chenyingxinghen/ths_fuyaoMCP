from __future__ import annotations

from dataclasses import dataclass


SUBJECTIVE_TERMS = {
    "火爆",
    "惨淡",
    "疯狂",
    "强势",
    "弱势",
    "恐慌",
    "乐观",
    "悲观",
    "极强",
    "极弱",
    "爆发",
    "崩盘",
    "活跃",
    "发酵",
    "意愿",
    "尚可",
    "明显",
    "中幅",
    "大幅",
    "小幅",
}


@dataclass(frozen=True)
class NeutralityFinding:
    term: str
    count: int


def find_subjective_terms(text: str) -> list[NeutralityFinding]:
    findings: list[NeutralityFinding] = []
    for term in sorted(SUBJECTIVE_TERMS):
        count = text.count(term)
        if count:
            findings.append(NeutralityFinding(term=term, count=count))
    return findings


def neutrality_report(text: str) -> dict[str, object]:
    findings = find_subjective_terms(text)
    return {
        "subjective_term_count": sum(item.count for item in findings),
        "findings": [
            {
                "term": item.term,
                "count": item.count,
            }
            for item in findings
        ],
    }
