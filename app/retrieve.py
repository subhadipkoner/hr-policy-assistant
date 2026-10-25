"""Retrieval over the policy chunks.

The corpus is one small handbook, so BM25 over the section text beats a vector
store here: it needs no model download, no API call and no warm-up, and on a
20-page document its weakness (no semantic reach) is covered by expanding the
handful of terms employees actually use for HR concepts.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .ingest import Chunk, write_index

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does",
    "for", "from", "has", "have", "how", "i", "if", "in", "is", "it", "its",
    "many", "me", "much", "my", "of", "on", "or", "our", "s", "shall", "should",
    "so", "that", "the", "their", "them", "there", "they", "this", "to", "we",
    "what", "when", "where", "which", "who", "will", "with", "you", "your",
}

# How employees phrase things, mapped onto the handbook's own vocabulary.
SYNONYMS = {
    "vacation": ["privilege", "leave", "pl"],
    "holiday": ["leave", "privilege"],
    "pto": ["leave", "privilege"],
    "sick": ["sick", "sl", "illness", "medical"],
    "ill": ["sick", "sl"],
    "casual": ["casual", "cl"],
    "maternity": ["maternity", "ml", "pregnancy"],
    "pregnant": ["maternity", "ml"],
    "emergency": ["emergengy", "el"],
    "salary": ["salary", "pay", "payday", "remuneration", "wages"],
    "pay": ["pay", "salary", "payday"],
    "payslip": ["payday", "salary"],
    "pf": ["provident", "fund", "epf"],
    "provident": ["provident", "fund", "pf"],
    "esi": ["esic", "insurance"],
    "notice": ["notice", "resignation", "termination"],
    "quit": ["resignation", "resign", "notice"],
    "resign": ["resignation", "notice"],
    "fired": ["termination", "terminate", "dismissal"],
    "appraisal": ["appraisal", "performance", "evaluation", "increment"],
    "promotion": ["promotion", "promote", "appraisal"],
    "raise": ["increment", "salary", "appraisal"],
    "dress": ["dress", "attire", "clothing", "uniform"],
    "wear": ["dress", "attire"],
    "late": ["late", "punctuality", "timekeeping"],
    "timing": ["hours", "timekeeping", "punctuality"],
    "hours": ["hours", "working", "shift"],
    "attendance": ["timekeeping", "punctuality", "attendance"],
    "travel": ["travelling", "allowance", "transport", "da"],
    "reimbursement": ["allowance", "travelling", "accommodation"],
    "hotel": ["accommodation", "lodging"],
    "phone": ["communications", "telephone", "mobile", "cell"],
    "mobile": ["communications", "telephone", "cell"],
    "internet": ["communications", "computer", "email"],
    "email": ["communications", "computer", "internet"],
    "social": ["communications", "media"],
    "safety": ["safety", "health", "accident", "hazard"],
    "accident": ["safety", "health", "injury", "accident"],
    "harassment": ["harassment", "discrimination", "conduct"],
    "discrimination": ["discrimination", "equity", "harassment"],
    "gift": ["gifts", "gratuities"],
    "confidential": ["disclosure", "confidential", "proprietary"],
    "nda": ["disclosure", "confidential"],
    "transfer": ["transfer", "relocation"],
    "probation": ["probationary", "categories", "employment"],
    "intern": ["categories", "employment", "trainee"],
    "warning": ["discipline", "disciplinary", "warning"],
    "misconduct": ["discipline", "disciplinary", "misconduct"],
    "laptop": ["property", "employer", "equipment", "computer"],
    "privacy": ["privacy", "personnel", "file"],
    "record": ["personnel", "file", "record"],
    "joining": ["orientation", "recruitment", "selection"],
    # The handbook spells these two as printed. Bridge the correct spelling
    # to the document's own so employees are not punished for typing it right.
    "vision": ["vission", "mission"],
    "goal": ["mission", "vission"],

    # Modern handbook vocabulary.
    "pto": ["pto", "paid", "time", "off", "leave", "vacation"],
    "wfh": ["remote", "home", "hybrid", "telecommuting"],
    "remote": ["remote", "home", "hybrid", "virtual"],
    "hybrid": ["hybrid", "remote", "office"],
    "okr": ["okr", "objective", "performance", "goal"],
    "diem": ["diem", "allowance", "travel", "expense"],
    "expense": ["expense", "reimbursement", "travel", "diem"],
    "reimburse": ["reimbursement", "expense", "claim"],
    "stipend": ["stipend", "allowance", "wellness"],
    "insurance": ["insurance", "health", "medical", "coverage", "group"],
    "dental": ["insurance", "health", "coverage"],
    "wellness": ["wellness", "health", "eap", "stipend"],
    "eap": ["eap", "assistance", "counselling", "wellness"],
    "erg": ["erg", "resource", "group", "diversity"],
    "dei": ["diversity", "equity", "inclusion", "dei"],
    "gdpr": ["gdpr", "privacy", "data", "security"],
    "security": ["security", "information", "data", "acceptable"],
    "monitoring": ["monitoring", "acceptable", "usage", "security"],
    "offboarding": ["offboarding", "exit", "separation", "resignation"],
    "onboarding": ["onboarding", "orientation", "induction", "probation"],
    "sabbatical": ["sabbatical", "specialized", "statutory", "absence"],
    "bereavement": ["bereavement", "specialized", "statutory", "absence"],
    "paternity": ["paternity", "specialized", "parental"],
    "caregiver": ["caregiver", "leave", "medical"],
    "tuition": ["tuition", "development", "certification", "education"],
    "certification": ["certification", "development", "tuition", "training"],
    "training": ["development", "certification", "training"],
    "grievance": ["grievance", "harassment", "redressal", "complaint"],
    "complaint": ["grievance", "harassment", "redressal"],
    "whistleblower": ["whistleblower", "appendix", "ethics", "reporting"],
    "ethics": ["ethics", "conduct", "integrity", "corruption"],
    "bribe": ["corruption", "gifts", "ethics", "conduct"],
    "ip": ["intellectual", "property", "inventions"],
    "patent": ["intellectual", "property", "inventions"],
    "background": ["background", "verification", "acquisition", "checks"],
    "shift": ["shift", "schedule", "attendance", "hours"],
    "break": ["break", "rest", "schedule"],
    "payroll": ["payroll", "compensation", "salary", "deduction"],
    "bonus": ["compensation", "incentive", "variable"],
    "deduction": ["deduction", "statutory", "payroll"],
}


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        if w in STOPWORDS:
            continue
        # Crude de-pluralising keeps "leaves"/"leave" and "hours"/"hour" together.
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def expand(tokens: list[str]) -> list[str]:
    expanded = list(tokens)
    for t in tokens:
        for extra in SYNONYMS.get(t, []):
            expanded.extend(tokenize(extra))
    return expanded


# A query word and the handbook's word often share a stem but not a suffix:
# "harassed" against "harassment", "monitored" against "monitoring". Matching
# on a shared prefix bridges those without a full stemmer, and without needing
# a synonym entry for every handbook that gets uploaded.
MIN_PREFIX = 5
MAX_PREFIX_MATCHES = 3


@dataclass
class Hit:
    chunk: Chunk
    score: float


class PolicyIndex:
    """BM25 with an extra weighting for terms that appear in a section title."""

    K1 = 1.4
    B = 0.72
    TITLE_BOOST = 2.5

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._tf: list[Counter] = []
        self._titles: list[set[str]] = []
        df: Counter = Counter()
        for c in chunks:
            tf = Counter(tokenize(c.text))
            title = set(tokenize(c.section))
            self._tf.append(tf)
            self._titles.append(title)
            # Title terms count toward document frequency too. "Mission" appears
            # only as a heading, and without this it would have no idf at all
            # and could never be matched.
            df.update(set(tf.keys()) | title)
        n = max(len(chunks), 1)
        self._idf = {
            term: math.log(1 + (n - count + 0.5) / (count + 0.5))
            for term, count in df.items()
        }
        lengths = [sum(tf.values()) for tf in self._tf] or [1]
        self._lengths = lengths
        self._avg_len = sum(lengths) / len(lengths)
        self._vocab = sorted(self._idf)

    def _prefix_matches(self, term: str) -> list[str]:
        """Index terms sharing a long prefix with an otherwise unknown term."""
        if len(term) < MIN_PREFIX:
            return []
        stem = term[:MIN_PREFIX]
        found = [v for v in self._vocab if v.startswith(stem) and v != term]
        found.sort(key=len)
        return found[:MAX_PREFIX_MATCHES]

    @classmethod
    def load(cls, pdf_path: Path, index_path: Path,
             rebuild: bool = False) -> "PolicyIndex":
        if rebuild or not index_path.exists():
            chunks = write_index(pdf_path, index_path)
        else:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
            chunks = [Chunk(**c) for c in raw["chunks"]]
        return cls(chunks)

    # A chunk scoring far below the best match is noise, not a second opinion.
    RELATIVE_FLOOR = 0.28

    def search(self, question: str, top_k: int = 5,
               min_score: float = 1.0) -> list[Hit]:
        terms = expand(tokenize(question))
        if not terms:
            return []
        for term in list(terms):
            if term not in self._idf:
                terms.extend(self._prefix_matches(term))
        counted = Counter(terms)
        hits: list[Hit] = []
        for i, chunk in enumerate(self.chunks):
            tf, length = self._tf[i], self._lengths[i]
            score = 0.0
            for term, q_count in counted.items():
                idf = self._idf.get(term)
                if idf is None:
                    continue
                freq = tf.get(term, 0)
                if term in self._titles[i]:
                    freq += self.TITLE_BOOST
                if not freq:
                    continue
                denom = freq + self.K1 * (
                    1 - self.B + self.B * length / self._avg_len)
                score += idf * (freq * (self.K1 + 1) / denom) * min(q_count, 2)
            if score > 0:
                hits.append(Hit(chunk, round(score, 3)))
        hits.sort(key=lambda h: h.score, reverse=True)
        if not hits:
            return []
        floor = max(min_score, hits[0].score * self.RELATIVE_FLOOR)
        kept = [h for h in hits if h.score >= floor]
        return (kept or hits[:1])[:top_k]
