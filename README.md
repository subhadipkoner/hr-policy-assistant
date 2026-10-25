# SKonnect Industry HR Policy Assistant

A retrieval-augmented question answering tool for the **SKonnect Industry** HR
policy manual. Employees ask in plain English and get an answer drawn only from
the handbook, with the section and page it came from so they can verify it
themselves.

![stack](https://img.shields.io/badge/python-3.12-blue) ![stack](https://img.shields.io/badge/FastAPI-0.115-teal)

## Why it is built this way

| Decision | Reason |
| --- | --- |
| Chunk on the document's own headings | Two layouts are detected automatically: flat ALL-CAPS headings and numbered sections (`9.0 Leave Architecture`). Every answer cites a section name and a page an employee can turn to |
| BM25 instead of a vector store | The corpus is a single handbook of a few dozen pages. BM25 needs no model download, no embedding API and no warm-up, and retrieval is exact on policy vocabulary |
| A hand-built HR synonym table | Bridges how employees speak ("vacation", "PTO", "fired", "raise") to the handbook's own words ("privilege leave", "termination", "increment") |
| Section titles count toward the index | "Mission" appears only as a heading, so a text-only index could never match it |
| Prefix matching on unknown query words | Bridges "harassed" to "harassment" and "monitored" to "monitoring" without a full stemmer, so it works on a handbook nobody has tuned for |
| Branding lives in `app/config.py` | One file carries the company name, which the API serves and the UI reads |
| Strict grounding prompt | The model is told to answer only from the supplied excerpts and to say plainly when the policy does not cover something |
| Extractive fallback | With no API key the app still works: it returns the relevant policy passage verbatim |
| Pluggable LLM provider | `app/llm.py` picks Gemini, Claude or extractive mode at runtime. A provider outage degrades to quoting the policy rather than failing the request |
| A model alias, not a pinned version | `gemini-flash-latest` tracks the current Flash model. Pinned names get retired: `gemini-2.5-flash` already returns 404 "no longer available to new users" |

## Running it

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>.

The handbook in `data/` is indexed on first request. To use a different PDF,
either drop it in `data/` or use **Replace** in the sidebar, which re-indexes
immediately. Cover pages and tables of contents are skipped, and running
footers are stripped.

### Pointing it at another company

Edit `app/config.py` (or set the env vars):

```python
COMPANY_NAME = "SKonnect Industry"
SOURCE_COMPANY_ALIASES = ["XYZ Technology Company", ...]  # rewritten to COMPANY_NAME
```

The supplied manual is a template that still carries its originating name, so
those aliases are rewritten as the text is extracted. Set
`REWRITE_COMPANY_NAME=0` to quote the PDF exactly as printed.

### Written answers (optional)

Without a key the assistant quotes the policy. With one it writes a short,
cited answer:

```bash
cp .env.example .env
# then set GEMINI_API_KEY=...
```

Get a key from [Google AI Studio](https://aistudio.google.com/apikey).

The provider is auto-detected: Gemini if `GEMINI_API_KEY` is set, otherwise
Claude if `ANTHROPIC_API_KEY` is set, otherwise extractive mode. Force one with
`LLM_PROVIDER=gemini|anthropic|none`.

| Variable | Default |
| --- | --- |
| `GEMINI_MODEL` | `gemini-flash-latest` |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` |

Never commit `.env`. It is gitignored; if a key has ever been pasted into a
chat, a terminal or a commit, rotate it.

## Layout

```
app/
  config.py    company branding and defaults
  ingest.py    PDF -> heading-aware, page-tagged chunks
  retrieve.py  BM25 + synonym expansion + title boost
  llm.py       provider layer (Gemini / Claude / none)
  answer.py    grounded prompt, citations, extractive fallback
  main.py      FastAPI routes and static hosting
static/        the single-page UI (no build step)
data/          the source PDF and the generated index
```

## API

| Route | Purpose |
| --- | --- |
| `POST /api/ask` | `{"question": "..."}` -> answer, sources, confidence |
| `GET /api/health` | document name, page/section/chunk counts, suggestions |
| `GET /api/sections` | the handbook's table of contents |
| `POST /api/upload` | replace the policy PDF and re-index |
| `GET /api/document` | download the source PDF |

## Rebuilding the index by hand

```bash
python -m app.ingest "data/SKonnect Industry HR Policy Manual.pdf"
```

Prints every chunk with its section, page range and size, which is the quickest
way to check a newly uploaded handbook chunked sensibly.

## Known limits

- Retrieval is lexical. A question with no word in common with the policy, and
  no entry in the synonym table, will score low. The UI labels those answers
  **Not clearly covered** rather than guessing.
- Rewriting the company name covers prose only. A contact string such as the
  ethics hotline number is left exactly as printed rather than invented.
- The **View PDF** link serves the original file, so the PDF itself still shows
  the template's company name. Only the indexed text is rebranded.
- **Gemini latency is the weak point.** On the key tested, a grounded answer
  took 45 to 60 seconds, and the API intermittently returns `503 high demand`.
  The request retries, then falls back to quoting the policy, so the app never
  fails; it just answers in the plainer mode. A paid-tier key or a Lite model
  should be considerably faster.
- `gemini-2.5-flash` returns `404 no longer available to new users`, and the
  Lite models reject `thinkingConfig` with a 400. `app/llm.py` drops that field
  and retries rather than carrying a per-model capability list.
- There is no authentication. Put it behind the company SSO or an internal
  network before sharing it with staff.
