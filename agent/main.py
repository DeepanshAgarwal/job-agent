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
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

# Ensure the repo root is on PYTHONPATH when running from any directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.ai.analyzer import Analyzer
from agent.ai.matcher import Matcher
from agent.ai.resume_parser import ResumeParser
from agent.ai.writer import Writer
from agent.notifier.slack import SlackNotifier
from agent.scraper.cutshort import CutshortScraper
from agent.scraper.foundit import FounditScraper
from agent.scraper.hirist import HiristScraper
from agent.scraper.instahyre import InstaHyreScraper
from agent.scraper.jobspy_scraper import JobSpyScraper
from agent.scraper.naukri import NaukriScraper
from agent.submitter.form_filler import FormFiller
from agent.tracker.database import Database
from agent.tracker.sheets import SheetsTracker

console = Console()
load_dotenv()

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
    if scrape_cfg.get("linkedin", {}).get("enabled") or scrape_cfg.get("indeed", {}).get("enabled"):
        scrapers.append(JobSpyScraper())
    return scrapers


async def run_pipeline(args: argparse.Namespace, config: dict) -> None:
    """Execute the full job application pipeline."""
    preferences = config["preferences"]
    profile = config["profile"]
    platforms = config["platforms"]

    db = Database()
    db.init_db()

    notifier = SlackNotifier()
    sheets = SheetsTracker()

    notifier.notify_start()

    # ── Step 1: Parse resume ─────────────────────────────────────────────────
    console.rule("[bold green]Step 1 — Resume Parsing[/bold green]")
    parser = ResumeParser()
    resume_path = profile.get("resume", {}).get("path", "config/resume.pdf")
    resume_dict = parser.parse(resume_path)
    resume_text = resume_dict.get("raw_text", "")
    console.print(f"  ✅ Resume parsed — {resume_dict.get('total_years', '?')} years of experience detected")

    # ── Step 2: Scrape jobs ──────────────────────────────────────────────────
    console.rule("[bold green]Step 2 — Scraping Jobs[/bold green]")
    scrapers = build_scraper_list(platforms)
    all_jobs: list[dict] = []

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        for scraper in scrapers:
            task = progress.add_task(f"Scraping {scraper.__class__.__name__}…", total=None)
            try:
                jobs = await scraper.scrape(preferences)
                new_jobs = [j for j in jobs if not db.is_seen(j["url"])]
                all_jobs.extend(new_jobs)
                progress.update(task, description=f"✅ {scraper.__class__.__name__} — {len(new_jobs)} new jobs")
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Scraper {scraper.__class__.__name__} failed: {exc}")
                progress.update(task, description=f"❌ {scraper.__class__.__name__} — failed")
            progress.stop_task(task)

    console.print(f"\n  📋 Total new jobs found: [bold]{len(all_jobs)}[/bold]")
    notifier.notify_scrape_complete(len(all_jobs))

    if not all_jobs:
        console.print("  [yellow]No new jobs found. Exiting.[/yellow]")
        return

    if args.scrape_only:
        for job in all_jobs:
            db.mark_seen(job)
        console.print("  [cyan]--scrape-only mode: jobs saved, no applications submitted.[/cyan]")
        return

    # ── Step 3: Embedding score ──────────────────────────────────────────────
    console.rule("[bold green]Step 3 — AI Embedding Scoring[/bold green]")
    matcher = Matcher()
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("Scoring jobs with sentence-transformers…", total=None)
        all_jobs = matcher.batch_score(resume_text, all_jobs)
        progress.update(task, description=f"✅ Scored {len(all_jobs)} jobs")

    # ── Step 4: Gemini analysis ──────────────────────────────────────────────
    console.rule("[bold green]Step 4 — Gemini Fit Analysis[/bold green]")
    analyzer = Analyzer()
    shortlisted: list[dict] = []
    for job in all_jobs:
        if job.get("match_score", 0) < preferences.get("min_match_score", 65):
            db.mark_seen(job)
            notifier.notify_skipped(job, f"Low embedding score: {job.get('match_score', 0):.1f}")
            continue
        try:
            analysis = analyzer.analyze(resume_dict, job, preferences)
            job.update(analysis)
            if analysis.get("should_apply"):
                shortlisted.append(job)
            else:
                db.mark_seen(job)
                notifier.notify_skipped(job, analysis.get("skip_reason", "AI skip"))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Gemini analysis failed for {job.get('url')}: {exc}")
            db.mark_seen(job)

    console.print(f"  🎯 Shortlisted: [bold]{len(shortlisted)}[/bold] / {len(all_jobs)} jobs")

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
        console.print("  [cyan]--dry-run mode: no applications submitted.[/cyan]")
        return

    console.print("\n  [bold yellow]⏳ Applying in 60 seconds — press Ctrl+C to abort.[/bold yellow]")
    try:
        for remaining in range(60, 0, -1):
            console.print(f"  Applying in {remaining}s…", end="\r")
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n  [red]Aborted by user.[/red]")
        return

    # ── Step 6–9: Generate cover letters + submit ────────────────────────────
    console.rule("[bold green]Step 6 — Generating Cover Letters & Submitting[/bold green]")
    writer = Writer()
    filler = FormFiller()
    applied_count = 0

    for job in shortlisted:
        try:
            cover_letter = writer.generate_cover_letter(resume_dict, job)
            result = await filler.apply_to_job(job, profile, cover_letter, dry_run=False)

            job["status"] = result.get("status", "unknown")
            job["notes"] = result.get("error", "")
            db.mark_seen(job)
            db.log_application(job)
            sheets.append_application(job)

            if result.get("status") == "applied":
                applied_count += 1
                notifier.notify_applied(job)
                console.print(f"  ✅ Applied: {job.get('company')} — {job.get('title')}")
            else:
                notifier.notify_failed(job, result.get("error", "Unknown error"))
                console.print(f"  ❌ Failed:  {job.get('company')} — {result.get('error')}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Submission failed for {job.get('url')}: {exc}")
            notifier.notify_failed(job, str(exc))

    # ── Final summary ────────────────────────────────────────────────────────
    stats = db.get_stats()
    console.rule("[bold green]Done[/bold green]")
    console.print(f"  Applied today: [bold green]{applied_count}[/bold green]")
    console.print(f"  Total in DB:   [bold]{stats.get('total_applied', 0)}[/bold]")
    notifier.notify_summary(stats)


def show_status(config: dict) -> None:
    """Print tracker statistics and exit."""
    db = Database()
    db.init_db()
    stats = db.get_stats()

    table = Table(title="Application Stats", show_lines=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Total Applied", str(stats.get("total_applied", 0)))
    for platform, count in stats.get("by_platform", {}).items():
        table.add_row(f"  {platform}", str(count))
    table.add_row("Applied", str(stats.get("by_status", {}).get("applied", 0)))
    table.add_row("Failed", str(stats.get("by_status", {}).get("failed", 0)))
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

    if not (args.run or args.dry_run or args.scrape_only):
        parser.print_help()
        return

    asyncio.run(run_pipeline(args, config))


if __name__ == "__main__":
    main()
