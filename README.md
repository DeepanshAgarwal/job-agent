# 🤖 Job Application Agent

An open-source, **free**, AI-powered job application agent that runs locally on your laptop.
It scrapes job listings from Indian and global platforms, scores them with AI, generates
personalised cover letters, fills application forms via Playwright, and tracks everything
in SQLite + Google Sheets with Slack notifications.

> **Semi-manual design**: you trigger the agent with one command. It scrapes, scores,
> shows you a 60-second Slack preview, then submits. You stay in control.

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

## 💻 Prerequisites

- Python 3.10 or higher → [Download](https://www.python.org/downloads/)
- Git → [Download](https://git-scm.com/downloads)
- A modern browser (Chromium is installed automatically by Playwright)

---

## 🚀 Quick Start

### Step 1 — Clone the repo

```bash
git clone https://github.com/DeepanshAgarwal/job-agent.git
cd job-agent
```

---

### Step 2 — Create & activate a virtual environment

> ⚠️ **Windows users**: the activation command differs by terminal. Pick yours below.

**Git Bash (recommended on Windows)**
```bash
python -m venv .venv
source .venv/Scripts/activate
```

**PowerShell**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```
> If PowerShell blocks execution, run this once first:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

**Command Prompt (cmd.exe)**
```cmd
python -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux**
```bash
python -m venv .venv
source .venv/bin/activate
```

You'll know it worked when your prompt shows `(.venv)` at the start.

---

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

> 📦 **Note**: the job scraping package is called `python-jobspy` on PyPI (not `jobspy`).
> The `requirements.txt` already has the correct name — just run the command above.

Then install the Playwright browser:

```bash
# macOS / Linux / Git Bash
playwright install chromium

# If that doesn't work on Windows
python -m playwright install chromium
```

---

### Step 4 — Add your resume & configure

```bash
# Copy your resume into the config folder
cp /path/to/your/resume.pdf config/resume.pdf

# Set up environment variables
cp .env.example .env
# Open .env and fill in your API keys (see Configuration section below)

# Fill in your personal details
# Open config/profile.yaml and replace placeholder values

# Set your job search criteria
# Open config/preferences.yaml and update roles, salary, location etc.
```

---

### Step 5 — Run the setup wizard

```bash
# Validate all API keys and configs
python setup.py --validate

# Log in to Indian job platforms (saves browser sessions — one-time only)
python setup.py --login all
```

---

### Step 6 — First dry run (no applications submitted)

```bash
python agent/main.py --dry-run
```

When you're happy with what you see:

```bash
python agent/main.py --run
```

---

## ⚙️ Configuration

All config lives in the `config/` folder. **You never need to touch the Python code.**

### `config/profile.yaml` — Your Details
```yaml
first_name: "Jane"
last_name: "Doe"
email: "jane@example.com"
phone: "+91-9876543210"
location: "Bangalore, India"
linkedin: "https://linkedin.com/in/janedoe"
github: "https://github.com/janedoe"
resume_path: "config/resume.pdf"

notice_period: "30 days"
current_ctc: "18 LPA"
expected_ctc: "28 LPA"
willing_to_relocate: false
work_authorization: "Indian Citizen"
```

### `config/preferences.yaml` — Job Search Criteria
```yaml
roles:
  - "Senior Backend Engineer"
  - "Python Developer"

keywords_must_have: ["Python"]
keywords_blacklist: ["PHP", "WordPress"]

salary:
  min_lpa: 25
  min_usd_yearly: 100000

seniority_levels: ["Mid", "Senior"]
company_blacklist: ["Infosys", "Wipro", "TCS"]

min_match_score: 65           # Only apply to jobs scoring >= 65%
max_applications_per_day: 15
posted_within_hours: 24
```

### `config/platforms.yaml` — Enable/Disable Platforms
```yaml
scrape_from:
  naukri:
    enabled: true
  instahyre:
    enabled: true
  linkedin:
    enabled: true
  # add or remove platforms as needed
```

### `.env` — API Keys
```bash
# Free at aistudio.google.com
GEMINI_API_KEY=your_gemini_api_key_here

# From Google Cloud Console (for Sheets tracking)
GOOGLE_SHEETS_CREDENTIALS_PATH=auth/google_credentials.json
GOOGLE_SHEET_ID=your_google_sheet_id_here

# From Slack App → Incoming Webhooks
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
```

---

## 📋 Usage

```bash
# Full pipeline — scrape → score → 60s Slack preview → apply
python agent/main.py --run

# Dry run — full pipeline but no forms are submitted
python agent/main.py --dry-run

# Only scrape and score, no applying
python agent/main.py --scrape-only

# Limit how many jobs to apply to this run
python agent/main.py --run --limit 5

# Show application stats from the local database
python agent/main.py --status

# Launch Streamlit dashboard (Phase 2)
streamlit run ui/dashboard.py
```

### Setup Wizard
```bash
# Log in to all Indian platforms and save browser sessions
python setup.py --login all

# Log in to a specific platform only
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
| Naukri.com | 🇮🇳 Indian | ✅ Playwright | ✅ | Yes (session saved) |
| Instahyre.com | 🇮🇳 Indian (tech) | ✅ Playwright | ✅ | Yes (session saved) |
| Hirist.com | 🇮🇳 Indian (tech) | ✅ Playwright | Via ATS | No |
| Cutshort.io | 🇮🇳 Indian (startup) | ✅ Playwright | Via ATS | No |
| Foundit.in | 🇮🇳 Indian | ✅ Playwright | Via ATS | No |
| LinkedIn | 🌐 Global | ✅ JobSpy | 🔜 Phase 4 | No |
| Indeed | 🌐 Global | ✅ JobSpy | Via ATS | No |
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
| Job scraping | python-jobspy | LinkedIn + Indeed unified |
| Storage | SQLite | Zero-config local DB |
| Sheets sync | gspread | Simple Google Sheets API |
| Notifications | Slack Incoming Webhook | Free, easy setup |
| Terminal UI | rich | Beautiful progress output |
| Logging | loguru | Structured, coloured logs |
| Dashboard | Streamlit | Quick local web UI |

---

## 📁 Project Structure

```
job-agent/
├── config/              ← YOUR files: profile, preferences, platforms, resume
├── agent/
│   ├── main.py          ← Entry point + orchestrator
│   ├── scraper/         ← One file per job platform
│   ├── ai/              ← Resume parser, matcher, analyzer, writer
│   ├── submitter/       ← Form filler + platform-specific handlers
│   ├── tracker/         ← SQLite + Google Sheets sync
│   └── notifier/        ← Slack notifications
├── auth/                ← Saved browser sessions (gitignored)
├── data/                ← SQLite DB + application screenshots
├── ui/                  ← Streamlit dashboard (Phase 2)
├── docs/                ← Architecture, guides, platform notes
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
Gemini API (1,500 req/day free)  →  ₹0 / $0 per month
python-jobspy                    →  ₹0 / $0 per month
Playwright + Chromium            →  ₹0 / $0 per month
SQLite                           →  ₹0 / $0 per month
Google Sheets API                →  ₹0 / $0 per month
Slack Incoming Webhook           →  ₹0 / $0 per month
─────────────────────────────────────────────────────
TOTAL                            →  ₹0 / $0 per month ✅
```

---

## 🐛 Troubleshooting

**`source .venv/bin/activate` not working on Windows?**
Use `source .venv/Scripts/activate` in Git Bash, or `.venv\Scripts\activate` in CMD/PowerShell.

**`playwright` command not found?**
Run `python -m playwright install chromium` instead.

**`python` not found?**
Try `python3` or `py` depending on your system's PATH setup.

**Package install SSL errors?**
```bash
pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

**`jobspy` not found error?**
The correct package name is `python-jobspy`. The `requirements.txt` already has this — make sure you pulled the latest version with `git pull` before installing.

---

## ⚠️ Disclaimer

This tool is for personal use to assist your own job search. Respect each
platform's Terms of Service. Use reasonable rate limits (the defaults are
conservative). Always review applications before they go out — the 60-second
Slack preview window exists for exactly this purpose. The author is not responsible
for any consequences of using this tool.

---

## 🤝 Contributing

Pull requests welcome! Areas that need the most help:
- Verifying and updating CSS selectors for each platform
- Adding LinkedIn Easy Apply support (Phase 4)
- Adding Ollama as a local LLM fallback
- Unit tests for core modules

Please open an issue before starting significant work.
