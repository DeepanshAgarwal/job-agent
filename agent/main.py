"""
agent/main.py — Orchestrator / Entry Point
==========================================
Run the full job application pipeline via CLI.

Usage:
    python agent/main.py --run           # Full pipeline
    python agent/main.py --dry-run       # Scrape + score, no submissions
    python agent/main.py --scrape-only   # Only scrape and save to DB
    python agent/main.py --limit 5       # Apply to at most 5 jobs
    python agent/main.py --status        # Show tracker stats and exit
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TaskID, TextColumn
from rich.table import Table

# Ensure the repo root is on PYTHONPATH when running from any directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.ai.analyzer import Analyzer
from agent.ai.matcher import Matcher
from agent.ai.resume_parser import ResumeParser
from agent.notifier.slack import SlackNotifier
from agent.scraper.cutshort import CutshortScraper
from agent.scraper.foundit import FounditScraper
from agent.scraper.hirist import HiristScraper
from agent.scraper.instahyre import InstaHyreScraper
from agent.scraper.internshala import InternshalasScraper
from agent.scraper.jobspy_scraper import JobSpyScraper
from agent.scraper.naukri import NaukriScraper
from agent.scraper.unstop import UnstopScraper
from agent.tracker.database import Database
from agent.tracker.sheets import SheetsTracker

console = Console()
load_dotenv()

# Log level: WARNING by default — only errors/warnings surface to the console.
# Run with --debug to restore full verbose output.
_DEBUG_MODE = "--debug" in sys.argv
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG" if _DEBUG_MODE else "WARNING",
    colorize=True,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
)

# Log level: WARNING by default so only errors surface at the console.
# Pass --debug to restore verbose output.
_DEBUG_MODE = "--debug" in sys.argv
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG" if _DEBUG_MODE else "WARNING",
    colorize=True,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
)

# Suppress noisy 3rd-party model-loading warnings ────────────────────────────
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

import logging as _stdlib_log  # noqa: E402  (after env vars)

# Route ALL stdlib logging through Loguru so formats/colours are consistent ──
_MUTE_PATTERNS = (
    "AFC is enabled",          # google-genai SDK automatic function calling noise
)

class _InterceptHandler(_stdlib_log.Handler):
    def emit(self, record: _stdlib_log.LogRecord) -> None:
        msg = record.getMessage()
        if any(pat in msg for pat in _MUTE_PATTERNS):
            return
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno  # type: ignore[assignment]
        frame, depth = _stdlib_log.currentframe(), 2
        while frame and frame.f_code.co_filename == _stdlib_log.__file__:
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

_stdlib_log.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

# Silence noisy 3rd-party libraries (they now flow through Loguru) ────────────
for _lib in (
    "transformers", "huggingface_hub", "sentence_transformers",
    "urllib3", "urllib3.connectionpool",   # jobspy HTTP connection spam
    "httpcore", "httpx",                   # any httpx-based scrapers
    "charset_normalizer",
):
    _stdlib_log.getLogger(_lib).setLevel(_stdlib_log.ERROR)

# JobSpy attaches its own StreamHandler to named loggers, bypassing our
# InterceptHandler.  Clear those handlers so messages propagate to root
# (where InterceptHandler lives) and use the unified Loguru format.
for _lib in ("JobSpy", "JobSpy:Linkedin", "JobSpy:Indeed"):
    _log = _stdlib_log.getLogger(_lib)
    _log.handlers.clear()
    _log.propagate = True

# Silence Google genai SDK internal chatter ("AFC is enabled..." etc.)
for _lib in ("google", "google.ai", "google.ai.generativelanguage",
             "google.generativeai", "google.auth"):
    _stdlib_log.getLogger(_lib).setLevel(_stdlib_log.WARNING)

# ── Config paths ─────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
CONFIG_DIR = ROOT / "config"


def load_config() -> dict:
    """Load all YAML configuration files and return as a single dict."""
    with open(CONFIG_DIR / "profile.yaml") as f:
        profile = yaml.safe_load(f)
    with open(CONFIG_DIR / "preferences.yaml") as f:
        preferences = yaml.safe_load(f)
    with open(CONFIG_DIR / "platforms.yaml") as f:
        platforms = yaml.safe_load(f)
    return {"profile": profile, "preferences": preferences, "platforms": platforms}


def print_banner() -> None:
    """Print a startup banner."""
    console.print(
        Panel.fit(
            "[bold cyan]🤖 Job Application Agent[/bold cyan]\n"
            "[dim]Automated job hunting — powered by AI & Playwright[/dim]",
            border_style="cyan",
        )
    )


def build_scraper_list(platforms: dict) -> list:
    """Return enabled scrapers based on platforms config."""
    scrape_cfg = platforms.get("scrape_from", {})
    scrapers = []
    if scrape_cfg.get("naukri", {}).get("enabled"):
        scrapers.append(NaukriScraper())
    if scrape_cfg.get("instahyre", {}).get("enabled"):
        scrapers.append(InstaHyreScraper())
    if scrape_cfg.get("hirist", {}).get("enabled"):
        scrapers.append(HiristScraper())
    if scrape_cfg.get("cutshort", {}).get("enabled"):
        scrapers.append(CutshortScraper())
    if scrape_cfg.get("foundit", {}).get("enabled"):
        scrapers.append(FounditScraper())
    if scrape_cfg.get("internshala", {}).get("enabled"):
        scrapers.append(InternshalasScraper())
    if scrape_cfg.get("unstop", {}).get("enabled"):
        scrapers.append(UnstopScraper())
    if scrape_cfg.get("linkedin", {}).get("enabled") or scrape_cfg.get("indeed", {}).get("enabled"):
        scrapers.append(JobSpyScraper())
    return scrapers


async def _apply_jobs(
    shortlisted: list[dict],
    session_id: str,
    profile: dict,
    db: "Database",
    sheets: "SheetsTracker",
    notifier: "SlackNotifier",
    dry_run: bool = False,
) -> int:
    """Apply to each shortlisted job and record all outcomes.  Returns applied count."""
    from agent.submitter.form_filler import FormFiller
    from agent.ai.writer import Writer

    writer = Writer()
    filler = FormFiller()
    applied_count = 0

    for job in shortlisted:
        url = job.get("url", "")
        outcome = "failed"
        error_reason = ""
        cover_letter = ""
        try:
            cover_letter = writer.generate_cover_letter(
                job.get("_resume_dict", {}), job
            )
            result = await filler.apply_to_job(job, profile, cover_letter, dry_run=dry_run)
            outcome = result.get("status", "failed")
            error_reason = result.get("error", "")
            job["status"] = outcome
            job["notes"] = error_reason
            if outcome == "applied":
                applied_count += 1
                notifier.notify_applied(job)
                console.print(f"  ✅ Applied:  {job.get('company')} — {job.get('title')}")
            elif outcome == "manual_apply":
                console.print(
                    f"  📋 Manual:  {job.get('company')} — {job.get('title')}\n"
                    f"              Apply manually: {url[:90]}"
                )
            else:
                notifier.notify_failed(job, error_reason or "Unknown error")
                console.print(
                    f"  ❌ Failed:   {job.get('company')} — {job.get('title')}\n"
                    f"              Reason: {error_reason or 'unknown'}"
                )
        except Exception as exc:  # noqa: BLE001
            outcome = "failed"
            error_reason = str(exc)
            job["status"] = outcome
            job["notes"] = error_reason
            logger.error(f"Submission error for {url}: {exc}")
            notifier.notify_failed(job, error_reason)
            console.print(
                f"  ❌ Error:    {job.get('company')} — {job.get('title')}\n"
                f"              Reason: {error_reason}"
            )
        finally:
            job["session_id"] = session_id  # needed by sheets.upsert_job
            db.record_outcome(session_id, url, outcome, failure_reason=error_reason, cover_letter=cover_letter)
            db.mark_seen(job, session_id=session_id)
            db.log_application(job, session_id=session_id)
            if outcome == "manual_apply":
                sheets.upsert_manual_apply(job, error_reason)
            else:
                sheets.upsert_job(job, outcome)

    return applied_count


async def run_retry(args: argparse.Namespace, config: dict) -> None:
    """Re-run the apply phase for failed/shortlisted jobs from the last session."""
    profile = config["profile"]
    db = Database()
    db.init_db()
    notifier = SlackNotifier()
    sheets = SheetsTracker()

    session_id = args.session or None
    retry_jobs = db.get_retry_jobs(session_id)

    if not retry_jobs:
        console.print("  [yellow]No retryable jobs found (no failed/shortlisted in last session).[/yellow]")
        return

    console.print(f"  Found [bold]{len(retry_jobs)}[/bold] job(s) to retry.")

    # Parse resume so writer can generate cover letters
    parser_r = ResumeParser()
    resume_path = profile.get("resume", {}).get("path", "config/resume.pdf")
    resume_dict = parser_r.parse(resume_path)
    for job in retry_jobs:
        job["_resume_dict"] = resume_dict

    new_session_id = db.create_session()
    console.rule("[bold green]Retry — Submitting[/bold green]")
    applied = await _apply_jobs(
        retry_jobs, new_session_id, profile, db, sheets, notifier, dry_run=args.dry_run
    )
    db.finish_session(new_session_id, {
        "total_scraped": 0,
        "total_scored": 0,
        "total_gemini": 0,
        "total_shortlisted": len(retry_jobs),
        "total_applied": applied,
        "total_failed": len(retry_jobs) - applied,
    })
    console.print(f"  Applied: [bold green]{applied}[/bold green] / {len(retry_jobs)}")
    sheets.sync_stats(db.get_stats())


async def run_pipeline(args: argparse.Namespace, config: dict) -> None:
    """Execute the full job application pipeline."""
    preferences = config["preferences"]
    profile = config["profile"]
    platforms = config["platforms"]

    db = Database()
    db.init_db()

    # Create a session record immediately — every run is tracked
    import hashlib as _hashlib
    _resume_path = profile.get("resume", {}).get("path", "config/resume.pdf")
    try:
        _resume_hash = _hashlib.sha256(Path(ROOT / _resume_path).read_bytes()).hexdigest()[:12]
    except Exception:
        _resume_hash = ""
    try:
        _prefs_text = (CONFIG_DIR / "preferences.yaml").read_text()
        _prefs_hash = _hashlib.sha256(_prefs_text.encode()).hexdigest()[:12]
    except Exception:
        _prefs_hash = ""
    session_id = db.create_session(resume_hash=_resume_hash, prefs_hash=_prefs_hash)

    notifier = SlackNotifier()
    sheets = SheetsTracker()

    notifier.notify_start()

    # ── Step 1: Parse resume ─────────────────────────────────────────────────
    console.rule("[bold green]Step 1 — Resume Parsing[/bold green]")
    parser = ResumeParser()
    resume_path = profile.get("resume", {}).get("path", "config/resume.pdf")
    resume_dict = parser.parse(resume_path)

    # Build a dense, noise-free text for embedding scoring.
    # Raw PDF text wastes token budget on contact info, whitespace, and
    # layout artefacts. Structured fields give the model pure signal.
    _exp_bullets = " ".join(
        desc
        for exp in (resume_dict.get("experience") or [])
        for desc in (exp.get("description") or [])
    )
    resume_text = " ".join(filter(None, [
        resume_dict.get("summary", ""),
        " ".join(resume_dict.get("skills") or []),
        _exp_bullets,
    ])) or resume_dict.get("raw_text", "")

    console.print(f"  ✅ Resume parsed — {resume_dict.get('total_years', '?')} years of experience detected")

    # ── Step 2: Scrape jobs ──────────────────────────────────────────────────
    console.rule("[bold green]Step 2 — Scraping Jobs[/bold green]")
    scrapers = build_scraper_list(platforms)
    all_jobs: list[dict] = []

    roles = preferences.get("roles", [])
    locations = preferences.get("locations", [])
    searches_per_scraper = max(1, len(roles) * len(locations))
    total_searches = len(scrapers) * searches_per_scraper

    async def _run_scraper(
        scraper,
        progress: Progress,
        global_task: TaskID,
        scraper_task: TaskID,
    ) -> tuple[str, list[dict]]:
        name = scraper.__class__.__name__
        short = name.replace("Scraper", "")

        def _advance() -> None:
            progress.advance(global_task)
            progress.advance(scraper_task)

        try:
            jobs = await scraper.scrape(preferences, on_search_done=_advance)
            progress.update(
                scraper_task,
                description=f"[green]✓ {short}[/green] — {len(jobs)} found",
                completed=searches_per_scraper,
            )
            return name, jobs
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Scraper {name} failed: {exc}")
            progress.update(
                scraper_task,
                description=f"[red]✗ {short}[/red] — error",
                completed=searches_per_scraper,
            )
            return name, []

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        console=console,
        transient=False,
    ) as progress:
        global_task = progress.add_task("[bold]Total[/bold]", total=total_searches)
        scraper_tasks = [
            (
                s,
                progress.add_task(
                    f"[dim]{s.__class__.__name__.replace('Scraper', '')}[/dim]",
                    total=searches_per_scraper,
                ),
            )
            for s in scrapers
        ]
        results = await asyncio.gather(
            *[_run_scraper(s, progress, global_task, tid) for s, tid in scraper_tasks]
        )

    for name, jobs in results:
        db.upsert_jobs(jobs)  # canonical job table — INSERT OR IGNORE
        new_jobs = [j for j in jobs if not db.is_seen(j["url"])]
        db.set_outcome_bulk(session_id, [j["url"] for j in new_jobs], "pending")
        all_jobs.extend(new_jobs)

    console.print(f"\n  📋 Total new jobs found: [bold]{len(all_jobs)}[/bold]")
    notifier.notify_scrape_complete(len(all_jobs))

    if not all_jobs:
        console.print("  [yellow]No new jobs found. Exiting.[/yellow]")
        return

    if args.scrape_only:
        db.set_outcome_bulk(session_id, [j["url"] for j in all_jobs], "pending")
        db.finish_session(session_id, {"total_scraped": len(all_jobs)})
        console.print("  [cyan]--scrape-only mode: jobs saved, no applications submitted.[/cyan]")
        return

    # ── Step 3: Embedding score ──────────────────────────────────────────────
    console.rule("[bold green]Step 3 — AI Embedding Scoring[/bold green]")

    if len(resume_text.strip()) < 200:
        console.print(
            f"  [bold red]⚠ Resume text is very short ({len(resume_text.strip())} chars).[/bold red]\n"
            "  [yellow]All jobs will score near 0. Check that config/resume.pdf exists and is text-based (not a scanned image).[/yellow]"
        )

    matcher = Matcher()
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("Scoring jobs with sentence-transformers…", total=None)
        all_jobs = matcher.batch_score(resume_text, all_jobs)
        progress.update(task, description=f"✅ Scored {len(all_jobs)} jobs")

    for job in all_jobs:
        db.record_embedding_score(session_id, job["url"], job.get("match_score", 0.0))

    # ── Step 4: Gemini analysis ──────────────────────────────────────────────
    console.rule("[bold green]Step 4 — Gemini Fit Analysis[/bold green]")

    # Build a keyword set from skills + role keywords for pre-filtering.
    # Jobs with zero overlap are genuinely irrelevant and skip Gemini entirely.
    _skill_keywords = {s.lower() for s in resume_dict.get("skills", [])}
    _role_keywords = {
        word.lower()
        for role in preferences.get("roles", [])
        for word in role.split()
        if len(word) > 3
    }
    _filter_keywords = _skill_keywords | _role_keywords

    def _has_keyword_match(job: dict) -> bool:
        text = (job.get("description", "") + " " + job.get("title", "")).lower()
        return any(kw in text for kw in _filter_keywords)

    # Split into keyword-matched (→ Gemini) vs irrelevant (→ low_score)
    keyword_matched = [j for j in all_jobs if _has_keyword_match(j)]
    keyword_rejected = [j for j in all_jobs if not _has_keyword_match(j)]

    db.set_outcome_bulk(session_id, [j["url"] for j in keyword_rejected], "low_score")
    for job in keyword_rejected:
        db.mark_seen(job, session_id=session_id)

    # Sort matched jobs by embedding score descending, then cap at max_gemini_calls.
    # Jobs beyond the cap are marked low_score — they'll re-surface next run if
    # better-scoring jobs have been exhausted.
    max_gemini = preferences.get("max_gemini_calls", 75)
    keyword_matched.sort(key=lambda j: j.get("match_score", 0.0), reverse=True)
    gemini_batch = keyword_matched[:max_gemini]
    gemini_overflow = keyword_matched[max_gemini:]

    db.set_outcome_bulk(session_id, [j["url"] for j in gemini_overflow], "deferred")

    console.print(
        f"  📊 Keyword filter: [bold]{len(keyword_matched)}[/bold] matched, "
        f"[bold]{len(gemini_batch)}[/bold] sent to Gemini "
        f"([dim]{len(keyword_rejected)} irrelevant, {len(gemini_overflow)} deferred to next run[/dim])"
    )

    analyzer = Analyzer()
    shortlisted: list[dict] = []
    skipped_low_score = len(keyword_rejected)
    skipped_ai = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=28),
        MofNCompleteColumn(),
        console=console,
    ) as _prog:
        _gtask = _prog.add_task("Waiting…", total=len(gemini_batch))
        for job in gemini_batch:
            _prog.update(
                _gtask,
                description=(
                    f"[cyan]{job.get('company', '?')[:22]}[/cyan]"
                    f"  {job.get('title', '')[:38]}"
                ),
            )
            try:
                analysis = analyzer.analyze(resume_dict, job, preferences)
                # Preserve embedding match_score; store Gemini's assessment under its own key
                job["gemini_score"] = analysis.get("match_score")
                job["match_reasons"] = analysis.get("match_reasons") or []
                job["skip_reason"] = analysis.get("skip_reason")
                gemini_score = job["gemini_score"]
                gemini_reasons = job["match_reasons"]
                if analysis.get("should_apply"):
                    db.record_gemini_decision(
                        session_id, job["url"],
                        gemini_score=gemini_score, gemini_reasons=gemini_reasons,
                        decision="apply", outcome="shortlisted",
                    )
                    job["session_id"] = session_id
                    sheets.upsert_job(job, "shortlisted")
                    job["_resume_dict"] = resume_dict  # pass through for cover-letter writer
                    shortlisted.append(job)
                else:
                    failure_reason = analysis.get("skip_reason") or "; ".join(gemini_reasons)
                    db.record_gemini_decision(
                        session_id, job["url"],
                        gemini_score=gemini_score, gemini_reasons=gemini_reasons,
                        decision="reject", outcome="ai_rejected",
                        failure_reason=failure_reason,
                    )
                    db.mark_seen(job, session_id=session_id)
                    skipped_ai += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Gemini analysis failed for {job.get('url')}: {exc}")
                db.record_outcome(session_id, job["url"], "error", failure_reason=str(exc))
                db.mark_seen(job, session_id=session_id)
            _prog.advance(_gtask)

    console.print(
        f"  🎯 Shortlisted: [bold]{len(shortlisted)}[/bold] / {len(all_jobs)} jobs  "
        f"([dim]low-score skipped: {skipped_low_score}, AI skipped: {skipped_ai}[/dim])"
    )

    if not shortlisted:
        console.print("  [yellow]No jobs passed AI screening. Exiting.[/yellow]")
        notifier.notify_summary(db.get_stats())
        return

    # Apply daily cap
    max_apps = preferences.get("max_applications_per_day", 15)
    if args.limit:
        max_apps = min(max_apps, args.limit)
    shortlisted = shortlisted[:max_apps]

    # ── Step 5: Preview + abort window ──────────────────────────────────────
    console.rule("[bold green]Step 5 — Preview (60-second abort window)[/bold green]")
    table = Table(title="Jobs about to be applied to", show_lines=True)
    table.add_column("Company", style="cyan")
    table.add_column("Role", style="white")
    table.add_column("Score", style="green")
    table.add_column("Source", style="dim")
    for job in shortlisted:
        table.add_row(
            job.get("company", "?"),
            job.get("title", "?"),
            f"{job.get('match_score', 0):.1f}",
            job.get("source", "?"),
        )
    console.print(table)
    notifier.notify_preview(shortlisted)

    if args.dry_run:
        for job in shortlisted:
            db.mark_seen(job, session_id=session_id)
        db.finish_session(session_id, {
            "total_scraped": len(all_jobs),
            "total_scored": len(all_jobs),
            "total_gemini": len(gemini_batch),
            "total_shortlisted": len(shortlisted),
        })
        console.print("  [cyan]--dry-run mode: no applications submitted, Sheets not updated.[/cyan]")
        return

    console.print("\n  [bold yellow]⏳ Applying in 60 seconds — press Ctrl+C to abort.[/bold yellow]")
    try:
        for remaining in range(60, 0, -1):
            console.print(f"  Applying in {remaining}s…", end="\r")
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n  [red]Aborted by user.[/red]")
        return

    # ── Step 6: Generate cover letters + submit ──────────────────────────────
    console.rule("[bold green]Step 6 — Generating Cover Letters & Submitting[/bold green]")
    applied_count = await _apply_jobs(
        shortlisted, session_id, profile, db, sheets, notifier, dry_run=False
    )
    failed_count = len(shortlisted) - applied_count

    # ── Finish session ────────────────────────────────────────────────────────
    db.finish_session(session_id, {
        "total_scraped": len(all_jobs),
        "total_scored": len(all_jobs),
        "total_gemini": len(gemini_batch),
        "total_shortlisted": len(shortlisted),
        "total_applied": applied_count,
        "total_failed": failed_count,
    })

    # ── Final summary ────────────────────────────────────────────────────────
    stats = db.get_stats()
    console.rule("[bold green]Done[/bold green]")
    console.print(f"  Applied:        [bold green]{applied_count}[/bold green]")
    console.print(f"  Failed:         [bold red]{failed_count}[/bold red]")
    console.print(f"  Session ID:     [dim]{session_id}[/dim]")
    console.print(f"  All-time total: [bold]{stats.get('total_applied_all_time', 0)}[/bold]")
    if failed_count:
        console.print(
            f"  [yellow]Tip: re-run failed jobs with [bold]--retry-failed[/bold][/yellow]"
        )
    sheets.sync_stats(stats)
    notifier.notify_summary(stats)


def show_status(config: dict) -> None:
    """Print tracker statistics and exit."""
    db = Database()
    db.init_db()
    stats = db.get_stats()

    table = Table(title="Application Stats", show_lines=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Session", stats.get("session_id", "—"))
    table.add_row("Scraped this run", str(stats.get("total_scraped", 0)))
    table.add_row("Shortlisted", str(stats.get("total_shortlisted", 0)))
    for outcome, count in stats.get("by_outcome", {}).items():
        table.add_row(f"  {outcome}", str(count))
    table.add_row("Applied (all-time)", str(stats.get("total_applied_all_time", stats.get("total_applied", 0))))
    console.print(table)


def main() -> None:
    """Parse CLI arguments and launch the pipeline."""
    parser = argparse.ArgumentParser(
        description="Job Application Agent — automatically applies to jobs on your behalf."
    )
    parser.add_argument("--run", action="store_true", help="Run the full pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Scrape + score but do not submit")
    parser.add_argument("--scrape-only", action="store_true", help="Only scrape and save jobs")
    parser.add_argument("--limit", type=int, default=None, help="Max applications to submit")
    parser.add_argument("--status", action="store_true", help="Show stats and exit")
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Re-run apply phase for failed/shortlisted jobs from the last session",
    )
    parser.add_argument(
        "--session", type=str, default=None, metavar="SESSION_ID",
        help="Session ID to retry (used with --retry-failed; defaults to most recent)",
    )
    args = parser.parse_args()

    print_banner()

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]Config file not found: {exc}[/red]")
        console.print("  Run [bold]python setup.py --validate[/bold] to check your setup.")
        sys.exit(1)

    if args.status:
        show_status(config)
        return

    if args.retry_failed:
        asyncio.run(run_retry(args, config))
        return

    if not (args.run or args.dry_run or args.scrape_only):
        parser.print_help()
        return

    asyncio.run(run_pipeline(args, config))


if __name__ == "__main__":
    main()
