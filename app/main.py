"""FastAPI app: serves the assistant UI and the ask/upload endpoints."""
from __future__ import annotations

import shutil
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import llm
from .answer import answer_question
from .config import COMPANY_NAME, COMPANY_TAGLINE, DEFAULT_SUGGESTIONS
from .retrieve import PolicyIndex

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
INDEX_PATH = DATA / "index.json"
DEFAULT_PDF = DATA / "SKonnect Industry HR Policy Manual.pdf"

app = FastAPI(title=f"{COMPANY_NAME} HR Policy Assistant", version="1.1.0")

state: dict = {"index": None, "pdf": DEFAULT_PDF}


def current_pdf() -> Path:
    pdf = state["pdf"]
    if pdf.exists():
        return pdf
    found = sorted(DATA.glob("*.pdf"))
    if not found:
        raise HTTPException(503, "No HR policy PDF has been uploaded yet.")
    state["pdf"] = found[0]
    return found[0]


def get_index(rebuild: bool = False) -> PolicyIndex:
    if state["index"] is None or rebuild:
        state["index"] = PolicyIndex.load(current_pdf(), INDEX_PATH,
                                          rebuild=rebuild)
    return state["index"]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=600)


def suggestions_for(index: PolicyIndex) -> list[str]:
    """Offer questions the loaded handbook can actually answer.

    A suggestion that returns "not covered" is worse than no suggestion, so
    each default is kept only if it retrieves something with real confidence.
    """
    keep = [q for q in DEFAULT_SUGGESTIONS
            if (hits := index.search(q, top_k=1)) and hits[0].score >= 5]
    return keep or DEFAULT_SUGGESTIONS[:3]


@app.get("/api/health")
def health() -> dict:
    index = get_index()
    pdf = current_pdf()
    return {
        "status": "ready",
        "company": COMPANY_NAME,
        "tagline": COMPANY_TAGLINE,
        "document": pdf.name,
        "pages": max(c.page_end for c in index.chunks),
        "sections": len({c.section for c in index.chunks}),
        "chunks": len(index.chunks),
        "provider": llm.active_provider(),
        "model": llm.model_name(),
        "suggestions": suggestions_for(index),
    }


@app.get("/api/sections")
def sections() -> dict:
    index = get_index()
    seen: dict[str, dict] = {}
    for chunk in index.chunks:
        name = chunk.section
        entry = seen.setdefault(name, {"section": name,
                                       "page_start": chunk.page_start,
                                       "page_end": chunk.page_end})
        entry["page_end"] = max(entry["page_end"], chunk.page_end)
    return {"sections": list(seen.values())}


@app.post("/api/ask")
def ask(request: AskRequest) -> dict:
    result = answer_question(get_index(), request.question)
    return {"question": request.question, "answer": result.text,
            "sources": result.sources, "mode": result.mode,
            "confidence": result.confidence,
            "document": current_pdf().name}


@app.post("/api/upload")
def upload(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")
    target = DATA / Path(file.filename).name
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    state["pdf"] = target
    index = get_index(rebuild=True)
    return {"document": target.name, "chunks": len(index.chunks),
            "pages": max(c.page_end for c in index.chunks),
            "sections": len({c.section for c in index.chunks})}


@app.get("/api/document")
def document() -> FileResponse:
    pdf = current_pdf()
    # inline, so the handbook opens in the browser instead of downloading
    return FileResponse(
        pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{pdf.name}"'})


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
