"""Turn an HR policy PDF into retrievable, citable chunks.

Handbooks are laid out in one of two ways in practice: flat ALL-CAPS headings,
or numbered sections ("9.0 Leave Architecture"). The style is detected per
document rather than hardcoded, so a new handbook can be uploaded without
touching this file.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from pypdf import PdfReader

from .config import COMPANY_NAME, REWRITE_COMPANY_NAME, SOURCE_COMPANY_ALIASES

# Style A: a flat run of shouted headings.
CAPS_HEADING = re.compile(r"^[A-Z][A-Z0-9 /&,\.\(\)\-\']{3,}$")
CAPS_SKIP = {"HR POLICY", "ALLOWANCE", "ACCOMMODATION",
             "GRADE MODE OF TRANSPORT DAILY"}

# Style B: "9.0 Comprehensive Leave Architecture & PTO Entitlements"
NUMBERED_HEADING = re.compile(r"^(\d{1,2})\.0\s+(\S.*)$")
APPENDIX_HEADING = re.compile(r"^(Appendix|Annexure)\b[:\s].*$", re.IGNORECASE)

# A running footer such as "Acme - Confidential Page 4 of 23".
FOOTER = re.compile(r"Page\s+\d+\s+of\s+\d+\s*$", re.IGNORECASE)

# A sub-heading inside a numbered section: a short title-style line that does
# not end in sentence punctuation. Used as a preferred split point only, so a
# false positive costs nothing worse than a slightly early break.
SUBHEADING = re.compile(r"^(?![a-z])[A-Z][^.!?;:]{6,70}$")

LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}

MAX_CHARS = 1400
OVERLAP_CHARS = 200


@dataclass
class Chunk:
    id: int
    section: str
    page_start: int
    page_end: int
    text: str

    @property
    def citation(self) -> str:
        pages = (f"p. {self.page_start}" if self.page_start == self.page_end
                 else f"pp. {self.page_start}-{self.page_end}")
        return f"{self.section} ({pages})"


def _clean(line: str) -> str:
    for bad, good in LIGATURES.items():
        line = line.replace(bad, good)
    line = line.replace("", "-").replace("•", "-")
    line = line.replace("’", "'").replace("‘", "'")
    line = line.replace("“", '"').replace("”", '"')
    line = line.replace("™", "").replace("�", "")
    line = line.replace("–", "-").replace("—", "-")
    line = line.replace(" ", " ")
    if REWRITE_COMPANY_NAME:
        for alias in SOURCE_COMPANY_ALIASES:
            line = re.sub(rf"\b{re.escape(alias)}\b", COMPANY_NAME, line)
    return line.rstrip()


def _is_front_matter(lines: list[str]) -> bool:
    """A cover or table of contents carries no policy and skews retrieval."""
    head = " ".join(lines[:4]).lower()
    return ("table of contents" in head or "document index" in head
            or "internal corporate policy document" in head)


def read_pages(pdf_path: Path) -> list[list[str]]:
    reader = PdfReader(str(pdf_path))
    pages: list[list[str]] = []
    for index, page in enumerate(reader.pages, start=1):
        lines = [_clean(l) for l in (page.extract_text() or "").split("\n")]
        if lines and lines[0].strip() == str(index):
            lines = lines[1:]            # bare page number as the first line
        lines = [l for l in lines if l.strip() and not FOOTER.search(l)]
        pages.append(lines)
    return pages


def _detect_style(pages: list[list[str]]) -> str:
    numbered = caps = 0
    for page_no, lines in enumerate(pages, start=1):
        if _is_front_matter(lines):
            continue
        for line in lines:
            stripped = line.strip()
            if NUMBERED_HEADING.match(stripped):
                numbered += 1
            elif (CAPS_HEADING.match(stripped) and stripped not in CAPS_SKIP
                    and len(stripped.split()) <= 9):
                caps += 1
    return "numbered" if numbered >= 4 and numbered >= caps else "caps"


def _split_long(section: str, page_start: int, page_end: int,
                lines: list[str]) -> list[tuple[str, int, int, str]]:
    """Pack a section's lines into chunks, preferring sub-heading breaks."""
    body = " ".join(lines).strip()
    if len(body) <= MAX_CHARS:
        return [(section, page_start, page_end, body)] if body else []

    # Offsets where a sub-heading begins, so a break can land there.
    breakpoints, cursor = [], 0
    for line in lines:
        if SUBHEADING.match(line.strip()):
            breakpoints.append(cursor)
        cursor += len(line) + 1

    parts: list[tuple[str, int, int, str]] = []
    start = 0
    while start < len(body):
        limit = min(start + MAX_CHARS, len(body))
        end = limit
        if limit < len(body):
            candidates = [b for b in breakpoints
                          if start + MAX_CHARS // 2 < b <= limit]
            if candidates:
                end = candidates[-1]
            else:
                dot = body.rfind(". ", start + MAX_CHARS // 2, limit)
                if dot != -1:
                    end = dot + 1
        piece = body[start:end].strip()
        if piece:
            parts.append((section, page_start, page_end, piece))
        if end >= len(body):
            break
        start = end if end in breakpoints else max(end - OVERLAP_CHARS,
                                                   start + 1)
    return parts


def build_chunks(pdf_path: Path) -> list[Chunk]:
    pages = read_pages(pdf_path)
    style = _detect_style(pages)

    section = "Introduction"
    start_page = 1
    buffer: list[str] = []
    raw: list[tuple[str, int, int, str]] = []
    # The page a section ends on is the last page that gave it text, not the
    # page where the next heading happens to appear.
    content_page = 1

    def flush() -> None:
        if buffer and len(" ".join(buffer).strip()) > 40:
            raw.extend(_split_long(section, start_page, content_page,
                                   list(buffer)))
        buffer.clear()

    for page_no, lines in enumerate(pages, start=1):
        if _is_front_matter(lines):
            continue
        for line in lines:
            stripped = line.strip()
            heading = None

            if style == "numbered":
                match = NUMBERED_HEADING.match(stripped)
                if match:
                    heading = f"{match.group(1)}.0 {match.group(2).strip()}"
                elif APPENDIX_HEADING.match(stripped):
                    heading = stripped
            elif (CAPS_HEADING.match(stripped) and stripped not in CAPS_SKIP
                    and len(stripped.split()) <= 9):
                heading = stripped.title()

            if heading:
                flush()
                section, start_page = heading, page_no
                content_page = page_no
            else:
                buffer.append(stripped)
                content_page = page_no
    flush()

    return [Chunk(i, s, a, b, t) for i, (s, a, b, t) in enumerate(raw)]


def write_index(pdf_path: Path, out_path: Path) -> list[Chunk]:
    chunks = build_chunks(pdf_path)
    out_path.write_text(
        json.dumps({"source": pdf_path.name,
                    "chunks": [asdict(c) for c in chunks]}, indent=1),
        encoding="utf-8")
    return chunks


if __name__ == "__main__":
    import sys
    pdf = Path(sys.argv[1] if len(sys.argv) > 1
               else "data/SKonnect Industry HR Policy Manual.pdf")
    made = write_index(pdf, Path("data/index.json"))
    print(f"{len(made)} chunks from {pdf.name}")
    for c in made:
        print(f"  [{c.id:>2}] {c.citation[:62]:<62} {len(c.text):>5} chars")
