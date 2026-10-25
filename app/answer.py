"""Compose an answer that cannot say anything the PDF does not say."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import llm
from .config import COMPANY_NAME
from .retrieve import Hit, PolicyIndex

SYSTEM = f"""You are the HR Policy Assistant for {COMPANY_NAME}. Employees ask \
you questions instead of reading the HR policy handbook.

Answer ONLY from the numbered policy excerpts supplied in the user message.

Rules:
- Every factual sentence must come from an excerpt. Never use outside knowledge
  of labour law or of what other companies do.
- Cite the excerpts you used inline as [1], [2] after the sentence they support.
- If the excerpts do not contain the answer, say exactly that the HR policy
  document does not cover it and suggest the employee contact their manager or
  the HR team. Do not guess.
- If the excerpts are partially relevant, answer the part you can and say
  plainly which part is not covered.
- Lead with the direct answer in one sentence, then the specifics, conditions
  and exceptions as short bullets. Keep it under 150 words.
- Write to the employee as "you", in plain professional English. Quote exact
  figures, day counts, grades, time limits and percentages verbatim.
- Do not use em dashes."""

PROMPT = """Employee question: {question}

Policy excerpts:
{context}

Answer the question using only these excerpts, with inline [n] citations."""


@dataclass
class Answer:
    text: str
    sources: list[dict]
    mode: str
    confidence: str


def _format_context(hits: list[Hit]) -> str:
    return "\n\n".join(
        f"[{i}] Section: {h.chunk.section} "
        f"(page {h.chunk.page_start}"
        f"{'' if h.chunk.page_start == h.chunk.page_end else '-' + str(h.chunk.page_end)})\n"
        f"{h.chunk.text}"
        for i, h in enumerate(hits, start=1))


def _sources(hits: list[Hit]) -> list[dict]:
    return [{"n": i, "section": h.chunk.section,
             "page_start": h.chunk.page_start, "page_end": h.chunk.page_end,
             "citation": h.chunk.citation, "score": h.score,
             "excerpt": h.chunk.text}
            for i, h in enumerate(hits, start=1)]


def _confidence(hits: list[Hit]) -> str:
    if not hits:
        return "none"
    top = hits[0].score
    return "high" if top >= 12 else "medium" if top >= 5 else "low"


NOT_COVERED = ("The HR policy document does not appear to cover that. "
               "Please contact your manager or the HR team.")


NOT_CONFIGURED_NOTE = (
    "_No language model is configured, so this is the relevant policy text "
    "quoted directly rather than a written summary. Open the source below "
    "for the full section._")


def _extractive(question: str, hits: list[Hit], confidence: str,
                note: str = NOT_CONFIGURED_NOTE) -> str:
    """Fallback: hand back the policy's own words.

    Only when the match is strong. A weak match quoted confidently reads as an
    answer, which is worse than saying the handbook does not cover it.
    """
    if not hits or confidence in ("none", "low"):
        nearby = ""
        if hits:
            names = ", ".join(dict.fromkeys(h.chunk.section
                                            for h in hits[:3]))
            nearby = (f"\n\nThe closest sections in the handbook are "
                      f"**{names}**, but none of them answers this directly.")
        return NOT_COVERED + nearby
    lead = hits[0]
    sentences = re.split(r"(?<=[.;])\s+", lead.chunk.text)
    body = " ".join(sentences[:4]).strip()
    return (f"From **{lead.chunk.citation}** of the HR policy [1]:\n\n"
            f"{body}\n\n{note}")


def _unavailable_note(exc: Exception) -> str:
    """Say why the written answer is missing, in the employee's terms."""
    detail = str(exc)
    if "429" in detail or "quota" in detail.lower():
        reason = ("The language model has run out of quota for now, so this "
                  "is the policy text quoted directly.")
    elif "503" in detail or "timed out" in detail.lower():
        reason = ("The language model is busy, so this is the policy text "
                  "quoted directly.")
    else:
        reason = ("The language model could not be reached, so this is the "
                  "policy text quoted directly.")
    return f"_{reason} Open the source below for the full section._"


def answer_question(index: PolicyIndex, question: str,
                    top_k: int = 5) -> Answer:
    question = (question or "").strip()
    if len(question) < 3:
        return Answer("Please ask a question about the HR policy.", [],
                      "invalid", "none")

    hits = index.search(question, top_k=top_k)
    sources = _sources(hits)
    confidence = _confidence(hits)

    if llm.active_provider() == "none":
        return Answer(_extractive(question, hits, confidence), sources,
                      "extractive", confidence)

    try:
        text = llm.complete(
            SYSTEM,
            PROMPT.format(question=question, context=_format_context(hits)))
        return Answer(text, sources, "generated", confidence)
    except llm.LLMUnavailable as exc:
        # Never fail the request: the policy passages are still the answer.
        return Answer(_extractive(question, hits, confidence,
                                  note=_unavailable_note(exc)),
                      sources, "extractive", confidence)
