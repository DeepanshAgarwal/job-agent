# 🤖 Job Application Agent

An open-source, **free**, AI-powered job application agent that runs locally on your laptop.
It scrapes job listings from Indian and global platforms, scores them with AI, generates
personalised cover letters, fills application forms via Playwright, and tracks everything
in SQLite + Google Sheets with Slack notifications.

> **Semi-manual design**: the agent scrapes and scores automatically, shows you a
> 60-second preview, then submits.  You stay in control.

---

## ✨ Features

- 🔍 **Scrapes 7 job platforms** — Naukri, Instahyre, Hirist, Cutshort, Foundit, LinkedIn, Indeed
- 🧠 **AI scoring** — sentence-transformers embedding similarity + Gemini fit analysis
- ✍️ **Cover letter generation** — ≤150 words, company-specific, zero buzzwords
- 🖱️ **Form filling** — Greenhouse, Lever, Naukri, Instahyre + AI-guided generic fallback
- 📊 **Tracking** — SQLite database + Google Sheets sync
- 🔔 **Slack notifications** — per-job and per-run summaries
- 💸 **100% free** — Gemini free tier + open-source libraries

---

## 🚀 Quick Start (5 Steps)

```bash
# 1. Clone and install
git clone https://github.com/DeepanshAgarwal/job-agent.git
cd job-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2. Add your resume
cp ~/path/to/resume.pdf config/resume.pdf

# 3. Configure
cp .env.example .env
# Edit .env and add GEMINI_API_KEY (free at aistudio.google.com)
# Edit config/profile.yaml with your details
# Edit config/preferences.yaml with your job search criteria

# 4. Validate setup
python setup.py --validate

# 5. First dry run (no applications submitted)
python agent/main.py --dry-run
```

---

## ⚙️ Configuration

### `config/profile.yaml` — Your Details
```yaml
personal:
  first_name: "Jane"
  email: "jane@example.com"
  phone: "+91-9876543210"

standard_answers:
  notice_period: "30 days"
  expected_ctc: "20 LPA"
```

### `config/preferences.yaml` — Job Search Criteria
```yaml
roles:
  - "Senior Backend Engineer"
  - "Python Developer"

keywords_must_have: ["Python"]
salary:
  min_lpa: 15
min_match_score: 65          # 0-100
max_applications_per_day: 15
```

### `config/platforms.yaml` — Enable/Disable Platforms
```yaml
scrape_from:
  naukri:
    enabled: true
  linkedin:
    enabled: true
```

---

## 📋 Usage

```bash
# Full pipeline (scrape → score → preview → apply)
python agent/main.py --run

# Dry run (no form submissions)
python agent/main.py --dry-run

# Only scrape and save to database
python agent/main.py --scrape-only

# Limit applications this run
python agent/main.py --run --limit 5

# Show tracker stats
python agent/main.py --status

# Streamlit dashboard
streamlit run ui/dashboard.py
```

### Setup Wizard
```bash
# Log in to Indian job platforms (saves browser sessions)
python setup.py --login all
python setup.py --login naukri

# Validate all API keys and configs
python setup.py --validate

# Create Google Sheets tabs and headers
python setup.py --init-sheets
```

---

## 🌐 Platform Support

| Platform | Type | Scraping | Auto-Apply | Login Required |
|----------|------|----------|------------|----------------|
| Naukri.com | Indian | ✅ Playwright | ✅ | Yes (session) |
| Instahyre.com | Indian (tech) | ✅ Playwright | ✅ | Yes (session) |
| Hirist.com | Indian (tech) | ✅ Playwright | Via ATS | No |
| Cutshort.io | Indian (startup) | ✅ Playwright | Via ATS | No |
| Foundit.in | Indian | ✅ Playwright | Via ATS | No |
| LinkedIn | Global | ✅ JobSpy | 🔜 Phase 4 | No |
| Indeed | Global | ✅ JobSpy | Via ATS | No |
| Greenhouse | ATS | — | ✅ Playwright | No |
| Lever | ATS | — | ✅ Playwright | No |
| Generic | Any | — | ✅ AI-guided | No |

---

## 🛠️ Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| AI / LLM | Google Gemini 1.5 Flash | Free: 1,500 req/day |
| Embeddings | sentence-transformers | Local, no API cost |
| Resume parsing | PyMuPDF + Gemini | Best PDF extraction |
| Browser automation | Playwright | Async, session support |
| Job scraping | JobSpy | LinkedIn + Indeed unified |
| Storage | SQLite | Zero-config local DB |
| Sheets sync | gspread | Simple Google Sheets API |
| Notifications | Slack Incoming Webhook | Free |
| Terminal UI | rich | Beautiful progress bars |
| Logging | loguru | Structured, coloured |
| Dashboard | Streamlit | Quick web UI |

---

## 📁 Project Structure

```
job-agent/
├── config/              ← Your profile, preferences, platform config
├── agent/
│   ├── main.py          ← Entry point + orchestrator
│   ├── scraper/         ← One file per job platform
│   ├── ai/              ← Resume parser, matcher, analyzer, writer
│   ├── submitter/       ← Form filler + platform handlers
│   ├── tracker/         ← SQLite + Google Sheets
│   └── notifier/        ← Slack
├── auth/                ← Browser sessions (gitignored)
├── data/                ← SQLite DB + screenshots
├── ui/                  ← Streamlit dashboard
├── docs/                ← Architecture + guides
└── setup.py             ← One-time setup wizard
```

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [Setup Guide](docs/setup-guide.md) | Step-by-step first-run instructions |
| [Architecture](docs/architecture.md) | System design, data flow, technology choices |
| [Implementation Plan](docs/implementation-plan.md) | Phased build checklist |
| [Platform Notes](docs/platform-notes.md) | Per-platform quirks and selector maintenance |

---

## 💰 Cost

```
Gemini API (1,500 req/day free)  →  $0/month
Playwright                       →  $0/month
JobSpy                           →  $0/month
SQLite                           →  $0/month
Google Sheets API                →  $0/month
Slack Webhook                    →  $0/month
─────────────────────────────────────────────
TOTAL                            →  $0/month ✅
```

---

## ⚠️ Disclaimer

This tool is for personal use to assist your own job search.  Respect each
platform's Terms of Service.  Use reasonable rate limits (the defaults are
conservative).  Always review applications before they go out — the 60-second
preview window exists for exactly this purpose.  The author is not responsible
for any consequences of using this tool.

---

## 🤝 Contributing

Pull requests welcome!  Areas that need the most help:
- Verifying and updating CSS selectors for each platform
- Adding LinkedIn Easy Apply support (Phase 4)
- Adding Ollama as a local LLM alternative
- Unit tests for core modules

Please open an issue before starting significant work.
