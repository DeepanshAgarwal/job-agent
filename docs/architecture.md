# System Architecture — Job Application Agent

## 1. System Overview

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         Job Application Agent                              │
│                                                                            │
│   config/           agent/              data/            external          │
│  ┌─────────┐       ┌────────┐          ┌──────────┐     ┌──────────────┐ │
│  │profile  │──────▶│ main   │─────────▶│job_agent │     │ Gemini Flash │ │
│  │prefs    │       │ .py    │          │  .db     │     │ (free API)   │ │
│  │platforms│       └────┬───┘          └──────────┘     └──────────────┘ │
│  └─────────┘            │                                                  │
│                         │                                ┌──────────────┐ │
│  config/                ▼                                │ Google       │ │
│  ┌─────────┐       ┌─────────────────────────────┐      │ Sheets API   │ │
│  │resume   │       │         Pipeline             │      └──────────────┘ │
│  │  .pdf   │       │  1. Parse Resume             │                        │
│  └─────────┘       │  2. Scrape Jobs              │      ┌──────────────┐ │
│                    │  3. Embed + Score             │      │ Slack        │ │
│                    │  4. Gemini Analysis           │      │ Webhook      │ │
│                    │  5. Preview / Abort           │      └──────────────┘ │
│                    │  6. Generate Cover Letters    │                        │
│                    │  7. Fill & Submit Forms       │      ┌──────────────┐ │
│                    │  8. Track Results             │      │ Playwright   │ │
│                    │  9. Notify                    │      │ Browser      │ │
│                    └─────────────────────────────────┘    └──────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Pipeline Steps (with approximate timing)

| Step | Module | Description | Typical Time |
|------|--------|-------------|--------------|
| 1 | `ai/resume_parser.py` | Parse PDF → structured dict (cached) | 5–30s first run |
| 2 | `scraper/*.py` | Scrape all enabled job boards | 2–10 min |
| 3 | `ai/matcher.py` | Batch embedding score all jobs | 10–60s |
| 4 | `ai/analyzer.py` | Gemini go/no-go decision per job | 1–3s/job |
| 5 | `main.py` | Rich preview table + 60s abort window | 60s |
| 6 | `ai/writer.py` | Generate cover letter per shortlisted job | 1–2s/job |
| 7 | `submitter/form_filler.py` | Playwright form fill + submit | 15–60s/job |
| 8 | `tracker/database.py` | SQLite write | <1s/job |
| 8 | `tracker/sheets.py` | Google Sheets append | 1–2s/job |
| 9 | `notifier/slack.py` | Slack webhook messages | <1s each |

---

## 3. Component Descriptions

### `agent/main.py` — Orchestrator
The entry point that ties every module together. Accepts CLI flags and
runs the pipeline sequentially.  Uses `rich` for terminal output and
`loguru` for file/structured logging.

### `agent/scraper/` — Job Discovery
Each scraper inherits from `BaseScraper` and implements `scrape(preferences) -> list[dict]`.
All scrapers must return an empty list (not raise) on any error.

| Scraper | Platform | Login Required |
|---------|----------|----------------|
| `jobspy_scraper.py` | LinkedIn + Indeed | No |
| `naukri.py` | Naukri.com | Yes (session) |
| `instahyre.py` | Instahyre.com | Yes (session) |
| `hirist.py` | Hirist.com | No |
| `cutshort.py` | Cutshort.io | No |
| `foundit.py` | Foundit.in | No |

### `agent/ai/` — Intelligence Layer
- **`resume_parser.py`** — PyMuPDF extracts text, Gemini structures it into JSON.  Result cached as `.parsed_cache.json`.
- **`matcher.py`** — `all-MiniLM-L6-v2` from sentence-transformers computes cosine similarity between resume and JD.  Runs in batch for efficiency.
- **`analyzer.py`** — Gemini Flash decides `should_apply` with structured JSON output.
- **`writer.py`** — Gemini Flash generates ≤150 word cover letters and custom Q&A answers.

### `agent/submitter/` — Form Submission
- **`session_manager.py`** — Saves/loads Playwright `storage_state` JSON for authenticated sessions.
- **`form_filler.py`** — URL pattern → platform detector → routes to correct handler.
- **`platforms/greenhouse.py`** — Standard Greenhouse ATS fields.
- **`platforms/lever.py`** — Standard Lever ATS fields.
- **`platforms/naukri_apply.py`** — Naukri native apply flow (session-based).
- **`platforms/instahyre_apply.py`** — Instahyre native apply flow.
- **`platforms/generic.py`** — Gemini-guided fallback for unknown ATSs.

### `agent/tracker/` — Persistence
- **`database.py`** — SQLite with two tables: `applications` and `seen_jobs`.
- **`sheets.py`** — Google Sheets sync via `gspread`.

### `agent/notifier/` — Alerts
- **`slack.py`** — All Slack messaging via plain HTTP POST to Incoming Webhook.

---

## 4. The Job Dict

Every job travels through the pipeline as a Python dict.  Fields are
stamped at different stages:

```python
{
    # Stamped by scraper
    "id": "abc123def456",          # MD5 of URL (first 16 chars)
    "title": "Senior Backend Engineer",
    "company": "Acme Corp",
    "location": "Bangalore / Remote",
    "url": "https://...",
    "description": "Full JD text…",
    "posted_at": "2024-01-15T08:00:00",
    "source": "naukri",

    # Stamped by matcher
    "match_score": 78.4,           # Embedding cosine similarity × 100

    # Stamped by analyzer
    "should_apply": True,
    "match_reasons": ["Python backend", "AWS experience"],
    "skip_reason": None,

    # Stamped by submitter
    "status": "applied",           # applied | dry_run | failed
    "notes": "",

    # Stamped by tracker
    "applied_at": "2024-01-15T09:30:00",
}
```

---

## 5. Technology Choices

| Technology | Reason |
|------------|--------|
| **Playwright** | Async, reliable, supports session persistence via `storage_state` |
| **Gemini 1.5 Flash** | Free tier: 1,500 req/day, 1M tokens/day — adequate for daily runs |
| **sentence-transformers** | Fast local embedding (no API cost), `all-MiniLM-L6-v2` is 80 MB |
| **PyMuPDF (fitz)** | Best-in-class PDF text extraction, MIT license |
| **SQLite** | Zero-infrastructure local DB; sufficient for personal use |
| **gspread** | Thin Google Sheets wrapper, battle-tested |
| **loguru** | Drop-in logging with structured output and file rotation |
| **rich** | Beautiful terminal progress bars and tables |
| **JobSpy** | Unified LinkedIn + Indeed scraper, actively maintained |

---

## 6. Third-Party Integrations

| Service | Purpose | Free Tier |
|---------|---------|-----------|
| Google Gemini API | LLM (resume parse, analyze, write) | 1,500 req/day |
| Google Sheets API | Visual application tracker | Free (service account) |
| Slack Incoming Webhook | Real-time notifications | Free |
| Playwright | Browser automation | Open source |
| JobSpy | LinkedIn + Indeed scraping | Open source |

---

## 7. Indian Platforms

All Indian platforms (Naukri, Instahyre, Hirist, Cutshort, Foundit) use
Playwright to drive a real browser.  Those requiring login (Naukri, Instahyre)
store sessions in `auth/{platform}_session.json`.

Session files must never be committed to Git (covered in `.gitignore`).

---

## 8. Session Management

```
python setup.py --login naukri
  └── Opens headed Chromium
  └── User logs in manually
  └── Press Enter
  └── Playwright saves storage_state to auth/naukri_session.json
      (includes cookies + localStorage)

python agent/main.py --run
  └── SessionManager.load_session("naukri", browser)
      └── Loads auth/naukri_session.json
      └── Returns authenticated BrowserContext
```

Sessions typically last 7–30 days.  Re-run `setup.py --login` when
a session expires.

---

## 9. AI Decision Pipeline

```
Resume text + Job description
        │
        ▼
sentence-transformers (local, free)
  └── cosine similarity → match_score (0-100)
        │
        ▼ (if score ≥ min_match_score)
Gemini Flash (cloud, free tier)
  └── Structured JSON: should_apply, match_reasons, skip_reason
        │
        ▼ (if should_apply == true)
Gemini Flash
  └── Cover letter (≤150 words)
  └── Custom Q&A answers
```

---

## 10. Storage Architecture

### SQLite (`data/job_agent.db`)
- **`seen_jobs`** — every scraped URL; prevents re-processing
- **`applications`** — every submitted application with full metadata

### Google Sheets
- **"Applications"** sheet — mirrors SQLite applications table
- **"Stats"** sheet — summary updated each run; easy to share/view on mobile
