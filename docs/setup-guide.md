# Setup Guide — Job Application Agent

## Prerequisites

- Python 3.12+
- `pip` or `uv`
- A PDF copy of your resume
- A Google account (for Gemini API — free)
- Optional: Google Cloud account (for Sheets), Slack workspace

---

## Step 1: Clone and Install Dependencies

```bash
git clone https://github.com/DeepanshAgarwal/job-agent.git
cd job-agent

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

---

## Step 2: Add Your Resume

Copy your resume PDF into the `config/` directory:

```bash
cp ~/path/to/resume.pdf config/resume.pdf
```

Delete the placeholder file:

```bash
rm config/resume.pdf.placeholder
```

---

## Step 3: Fill in Your Profile

Edit `config/profile.yaml` with your real details:

```yaml
personal:
  first_name: "Jane"
  last_name: "Doe"
  email: "jane@example.com"
  phone: "+91-9876543210"
  location: "Bangalore, India"

links:
  linkedin: "https://linkedin.com/in/janedoe"
  github: "https://github.com/janedoe"
```

Edit `config/preferences.yaml` to match your job search criteria:

```yaml
roles:
  - "Senior Backend Engineer"
  - "Python Developer"

salary:
  min_lpa: 18

keywords_must_have:
  - "Python"
```

---

## Step 4: Get a Free Gemini API Key

1. Go to [https://aistudio.google.com](https://aistudio.google.com)
2. Sign in with your Google account
3. Click **Get API key** → **Create API key**
4. Copy the key

---

## Step 5: Configure Environment Variables

Copy the example `.env` file:

```bash
cp .env.example .env
```

Edit `.env` and fill in your keys:

```
GEMINI_API_KEY=AIzaSy...your_key_here...
```

The Slack and Google Sheets variables are optional — the agent works
without them but won't send notifications or sync to Sheets.

---

## Step 6: Set Up Google Sheets (Optional)

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project
3. Enable **Google Sheets API** and **Google Drive API**
4. Create a **Service Account** → download JSON key → save as `auth/google_credentials.json`
5. Create a new Google Sheet → copy the Sheet ID from the URL
6. Share the sheet with the service account email (Editor access)
7. Add to `.env`:
   ```
   GOOGLE_SHEETS_CREDENTIALS_PATH=auth/google_credentials.json
   GOOGLE_SHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
   ```
8. Run: `python setup.py --init-sheets`

---

## Step 7: Set Up Slack Notifications (Optional)

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Create a new app → choose **Incoming Webhooks**
3. Activate webhooks → Add New Webhook to Workspace
4. Copy the Webhook URL and add to `.env`:
   ```
   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
   ```

---

## Step 8: Log In to Indian Job Platforms (Optional)

For Naukri, Instahyre, and other Indian platforms, save your login session:

```bash
# Log in to all platforms one by one
python setup.py --login all

# Or a specific platform
python setup.py --login naukri
```

A browser window will open.  Log in manually, then press **Enter** in the
terminal to save the session.

---

## Step 9: Validate Your Setup

```bash
python setup.py --validate
```

You should see ✅ for all configured items.

---

## Step 10: First Dry Run

Run the agent without submitting any applications:

```bash
python agent/main.py --dry-run
```

Review the output:
- Check the scraped jobs look relevant
- Verify match scores seem reasonable
- Confirm cover letters (if shown) look good

---

## Step 11: Full Run

When you're satisfied with the dry run:

```bash
python agent/main.py --run
```

The agent will:
1. Parse your resume
2. Scrape all enabled job boards
3. Score and filter jobs with AI
4. Show you a 60-second preview (press Ctrl+C to abort)
5. Generate cover letters and submit applications
6. Log results to SQLite and Google Sheets
7. Send Slack notifications

---

## Daily Automation (Optional)

### macOS/Linux cron

```bash
crontab -e
# Add this line to run at 8 AM on weekdays:
0 8 * * 1-5 cd /path/to/job-agent && .venv/bin/python agent/main.py --run >> data/agent.log 2>&1
```

### Windows Task Scheduler

Create a Basic Task:
- Trigger: Daily at 8:00 AM
- Action: Start a program
- Program: `C:\path\to\job-agent\.venv\Scripts\python.exe`
- Arguments: `agent/main.py --run`
- Start in: `C:\path\to\job-agent`

---

## Dashboard

Launch the Streamlit dashboard for a visual overview:

```bash
streamlit run ui/dashboard.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Troubleshooting

| Issue | Solution |
|-------|---------|
| `Resume not found` | Copy your PDF to `config/resume.pdf` |
| `GEMINI_API_KEY not set` | Add to `.env` file |
| Scraper returns 0 jobs | Selectors may have changed — see `docs/platform-notes.md` |
| Session expired | Re-run `python setup.py --login <platform>` |
| `playwright install` failed | Run `playwright install chromium --with-deps` |
