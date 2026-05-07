# Platform Notes — Job Application Agent

Notes on the scraping approach, quirks, and known limitations for each
supported job platform.

---

## Naukri.com

**Scraper**: `agent/scraper/naukri.py`
**Apply handler**: `agent/submitter/platforms/naukri_apply.py`

- Login is **required** to see full job descriptions and to apply.
- Uses Playwright with a saved session (`auth/naukri_session.json`).
- Sessions typically expire after 30 days — re-run `python setup.py --login naukri`.
- Naukri uses a React SPA; wait for `domcontentloaded` may not be enough on
  slow connections — add extra `page.wait_for_selector` calls if needed.
- The "Apply" button on some listings opens a modal rather than a new page.
  The current handler clicks the button and waits; verify this works for your
  target roles.
- Naukri blocks automated traffic aggressively. If you see CAPTCHAs, add
  random delays between page loads.

**TODO selectors to verify**:
- `article.jobTuple` — job card container
- `a.title` — job title link
- `a.subTitle` — company name

---

## Instahyre.com

**Scraper**: `agent/scraper/instahyre.py`
**Apply handler**: `agent/submitter/platforms/instahyre_apply.py`

- Login required for applying; optional for scraping public listings.
- Instahyre is tech-focused and generally has higher-quality listings than Naukri.
- Good for senior/lead engineering roles (5+ years experience).
- The site uses server-side rendering so DOM parsing is reliable.
- Apply flow: clicking "Apply" or "Express Interest" uses your stored Instahyre
  profile — minimal additional form fields.

**TODO selectors to verify**:
- `div.job-card` — job card
- `h2.job-title` — title
- `span.company-name` — company

---

## Hirist.com

**Scraper**: `agent/scraper/hirist.py`

- No login required for scraping.
- Technology-only platform — no non-tech roles.
- Small but curated job set; lower volume than Naukri/LinkedIn.
- Apply links on Hirist redirect to the company's own ATS page (Greenhouse,
  Lever, or company career page) — the generic handler or a specific ATS
  handler will then take over.

**TODO selectors to verify**:
- `div.job-listing` — job card
- `h2.job-title a` — title + link

---

## Cutshort.io

**Scraper**: `agent/scraper/cutshort.py`

- No login required for browsing; login required for applying.
- Startup-focused platform — good for roles at Series A–C companies.
- Cutshort may expose a public GraphQL API (check Network tab in DevTools).
  If so, an API-based approach would be faster and more reliable than DOM
  scraping.
- The site uses client-side rendering (React); `wait_until="networkidle"` is
  used to ensure jobs load before parsing.

**TODO selectors to verify**:
- `div[data-cy='job-listing-card']` — job card
- `h2[data-cy='job-title']` — title

---

## Foundit.in (formerly Monster India)

**Scraper**: `agent/scraper/foundit.py`

- No login required for scraping.
- General-purpose platform (not tech-only) — use keyword filters aggressively.
- The site was rebranded from Monster India; some legacy URLs may still work.
- Pagination works via a "Next" button — the scraper handles up to 3 pages.

**TODO selectors to verify**:
- `div.srpResultCardContainer` — job card
- `h3.jobTitle a` — title + link
- `span.companyName` — company

---

## LinkedIn

**Scraper**: `agent/scraper/jobspy_scraper.py` (via JobSpy)

- No Playwright required — JobSpy uses the LinkedIn public job search API.
- LinkedIn Easy Apply is not yet automated in this agent (Phase 4 feature).
  Currently, the agent will navigate to the Easy Apply form via the generic
  handler when `linkedin_easy_apply` is enabled.
- LinkedIn aggressively rate-limits; JobSpy handles this with built-in delays.
- Results are limited to ~50 per search term/location combination.

---

## Indeed

**Scraper**: `agent/scraper/jobspy_scraper.py` (via JobSpy)

- No Playwright required — JobSpy handles Indeed scraping.
- Indeed's public job pages are apply-redirected to company ATSs (Greenhouse,
  Lever, etc.) or to Indeed's own Easy Apply — the form filler handles both.
- Location filtering is handled by JobSpy; set `country_indeed="IN"` in the
  scraper if you want India-specific results.

---

## Greenhouse (ATS)

**Apply handler**: `agent/submitter/platforms/greenhouse.py`

- Greenhouse is one of the most common ATSs for funded startups globally.
- No login required — forms are public-facing.
- Form fields are well-standardised: `#first_name`, `#last_name`, `#email`,
  `#phone`, resume upload, cover letter.
- Some companies add custom questions via additional `<input>` fields — the
  generic handler can be called as a fallback for these.

---

## Lever (ATS)

**Apply handler**: `agent/submitter/platforms/lever.py`

- Lever is another common ATS, similar to Greenhouse in predictability.
- Uses `data-qa` attributes as selectors (more stable than class-based).
- No login required.
- Cover letter field is labelled "Additional Information" — adjust if a
  specific company uses a different label.

---

## Selector Maintenance

All `_SELECTORS` dicts in scrapers and handlers are marked with
`# TODO: Verify selectors` comments.  When a scraper breaks:

1. Open the site in Chrome DevTools
2. Inspect the element you need
3. Find a stable selector (prefer `data-*` attributes over class names)
4. Update the `_SELECTORS` dict in the relevant file
5. Test with `python agent/main.py --scrape-only`
