# Implementation Plan — Phased Build

## Phase 1: Core Pipeline (Week 1–2)

### Goal
A working end-to-end pipeline that scrapes LinkedIn/Indeed, scores jobs, and
generates cover letters — without any form submission.

### Tasks
- [x] Project scaffold (folders, configs, requirements.txt)
- [x] `config/profile.yaml` — personal details
- [x] `config/preferences.yaml` — job search preferences
- [x] `config/platforms.yaml` — enable/disable platforms
- [x] `agent/ai/resume_parser.py` — PDF → structured dict
- [x] `agent/ai/matcher.py` — sentence-transformers scoring
- [x] `agent/ai/analyzer.py` — Gemini go/no-go decision
- [x] `agent/ai/writer.py` — cover letter generation
- [x] `agent/scraper/jobspy_scraper.py` — LinkedIn + Indeed
- [x] `agent/tracker/database.py` — SQLite persistence
- [x] `agent/main.py` — orchestrator with `--dry-run`
- [ ] **Manual test**: run `python agent/main.py --dry-run` and verify output
- [ ] Fill in real details in `config/profile.yaml`
- [ ] Add real resume to `config/resume.pdf`
- [ ] Set `GEMINI_API_KEY` in `.env`

---

## Phase 2: Indian Platforms (Week 2–3)

### Goal
Add all five Indian job board scrapers with login sessions.

### Tasks
- [x] `agent/submitter/session_manager.py` — session save/load
- [x] `setup.py --login` — interactive login wizard
- [x] `agent/scraper/naukri.py` — Naukri.com Playwright scraper
- [x] `agent/scraper/instahyre.py` — Instahyre.com scraper
- [x] `agent/scraper/hirist.py` — Hirist.com scraper
- [x] `agent/scraper/cutshort.py` — Cutshort.io scraper
- [x] `agent/scraper/foundit.py` — Foundit.in scraper
- [ ] **Manual test**: run `python setup.py --login naukri` and save session
- [ ] **Selector verification**: check each scraper's selectors against live site
- [ ] Test each scraper individually with `--scrape-only`

---

## Phase 3: Form Filling (Week 3–4)

### Goal
Actually submit applications via Playwright on real ATS systems.

### Tasks
- [x] `agent/submitter/form_filler.py` — platform detector + router
- [x] `agent/submitter/platforms/greenhouse.py` — Greenhouse handler
- [x] `agent/submitter/platforms/lever.py` — Lever handler
- [x] `agent/submitter/platforms/naukri_apply.py` — Naukri apply
- [x] `agent/submitter/platforms/instahyre_apply.py` — Instahyre apply
- [x] `agent/submitter/platforms/generic.py` — AI fallback
- [ ] **Selector verification**: test Greenhouse on a real listing (dry-run)
- [ ] **Selector verification**: test Lever on a real listing (dry-run)
- [ ] Set up Google Sheets service account
- [ ] Run `python setup.py --init-sheets`
- [x] `agent/tracker/sheets.py` — Google Sheets sync
- [x] `agent/notifier/slack.py` — Slack notifications
- [ ] Set `SLACK_WEBHOOK_URL` in `.env`
- [ ] **Full run test**: `python agent/main.py --run --limit 1`

---

## Phase 4: Polish & UI (Week 4+)

### Goal
Dashboard, monitoring, and reliability improvements.

### Tasks
- [x] `ui/dashboard.py` — Streamlit dashboard
- [ ] Add daily cron trigger (local `cron` or Task Scheduler on Windows)
- [ ] Screenshot review workflow (check `data/screenshots/` after each run)
- [ ] Add email fallback notifications (Gmail API or SMTP)
- [ ] Tune `min_match_score` threshold based on observed results
- [ ] Add Ollama support as a Gemini alternative
- [ ] Expand `generic.py` with more robust field detection heuristics
- [ ] Write unit tests for `database.py`, `matcher.py`, `resume_parser.py`
- [ ] Add GitHub Actions workflow for scrape-only daily runs

---

## Selector Maintenance Note

CSS selectors on dynamic websites **will break** as sites update their HTML.
When a scraper starts returning 0 results:

1. Open the site in DevTools
2. Find the element you need
3. Copy the best CSS selector
4. Update the `_SELECTORS` dict in the relevant scraper file
5. Look for the `# TODO: Verify selectors` comments as a checklist
