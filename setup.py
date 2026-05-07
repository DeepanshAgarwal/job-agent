"""
setup.py — One-time setup wizard for the Job Application Agent.

Usage:
    python setup.py --login all          # Log in to all enabled platforms
    python setup.py --login naukri       # Log in to a specific platform
    python setup.py --validate           # Check all API keys and configs
    python setup.py --init-sheets        # Create Google Sheets tabs and headers

How login works (CDP approach):
    Chrome is launched automatically with a remote debugging port.  Playwright
    then connects to that already-running Chrome instance and navigates to the
    platform login page.  Because Chrome was not launched by Playwright, Google
    OAuth works without any blocks or "browser may not be secure" warnings.
    Chrome is closed automatically once the session is saved.

Platforms are read from config/platforms.yaml.  Only platforms with
``login_required: true`` and ``enabled: true`` are included in ``--login all``.
"""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()
console = Console()

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_CDP_PORT = 9222

# Best available free-tier Gemini model (no billing required)
_GEMINI_MODEL = "gemini-1.5-flash-8b"

# Standard Chrome executable locations per OS
_CHROME_PATHS = [
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


def _load_platforms() -> dict:
    """Load platform config from config/platforms.yaml.

    Returns:
        Dict of platform name → config for all platforms that require login
        and are enabled under ``scrape_from``.
    """
    platforms_file = ROOT / "config" / "platforms.yaml"
    if not platforms_file.exists():
        return {}
    with platforms_file.open() as f:
        config = yaml.safe_load(f)
    result = {}
    for name, cfg in (config.get("scrape_from") or {}).items():
        if cfg.get("enabled") and cfg.get("login_required") and cfg.get("login_url"):
            result[name] = cfg
    return result


def _find_chrome() -> str | None:
    """Return path to the Chrome executable, or None if not found."""
    for path in _CHROME_PATHS:
        if Path(path).exists():
            return path
    return None


def _launch_chrome_with_cdp(chrome_exe: str) -> subprocess.Popen:
    """Launch Chrome with remote debugging enabled (non-blocking).

    A dedicated profile directory is used so this Chrome instance never
    conflicts with the user's normal Chrome.

    Args:
        chrome_exe: Full path to the Chrome executable.

    Returns:
        The Popen process handle so the caller can terminate Chrome later.
    """
    debug_profile = ROOT / "auth" / "chrome-cdp-profile"
    debug_profile.mkdir(parents=True, exist_ok=True)

    return subprocess.Popen(
        [
            chrome_exe,
            f"--remote-debugging-port={_CDP_PORT}",
            f"--user-data-dir={debug_profile}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def login_platform(platform: str, login_url: str) -> None:
    """Launch Chrome via CDP, navigate to the platform login page, wait for
    manual login, save the session cookies to disk, then close Chrome.

    Because Playwright connects to an already-running Chrome (rather than
    launching its own), Google OAuth works without any blocks or warnings.

    Args:
        platform:  Short platform name, e.g. ``"naukri"``.
        login_url: The login page URL for this platform.
    """
    from playwright.async_api import async_playwright

    from agent.submitter.session_manager import SessionManager

    chrome_exe = _find_chrome()
    if not chrome_exe:
        console.print(
            "[red]❌ Google Chrome not found.[/red]\n"
            "   Install it from https://www.google.com/chrome/ then re-run."
        )
        return

    console.print(f"\n[bold]Logging in to {platform}[/bold]")
    chrome_proc = _launch_chrome_with_cdp(chrome_exe)

    # Give Chrome time to start before connecting
    await asyncio.sleep(2)

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(f"http://localhost:{_CDP_PORT}")
        except Exception:
            console.print(
                "[red]❌ Could not connect to Chrome.[/red]\n"
                "   Make sure Chrome opened successfully and try again."
            )
            chrome_proc.terminate()
            return

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto(login_url, wait_until="domcontentloaded", timeout=30_000)
        console.print("Log in using your Google account or email/password.")

        input("\n  Press Enter once you are fully logged in… ")

        session_mgr = SessionManager()
        await session_mgr.save_session(platform, context)
        console.print(f"[green]✅ Session saved for {platform}[/green]")

        await browser.close()

    # Close Chrome now that the session is captured
    chrome_proc.terminate()
    console.print("[dim]Chrome closed.[/dim]")


def validate_config() -> None:
    """Check that all required API keys and config files are present."""
    console.rule("[bold]Validating configuration[/bold]")
    all_ok = True

    # API keys
    checks = {
        "GEMINI_API_KEY":    os.getenv("GEMINI_API_KEY"),
        "GOOGLE_SHEET_ID":   os.getenv("GOOGLE_SHEET_ID"),
        "SLACK_WEBHOOK_URL": os.getenv("SLACK_WEBHOOK_URL"),
    }
    for key, val in checks.items():
        if val and val not in ("your_gemini_api_key_here", "your_google_sheet_id_here"):
            console.print(f"  [green]✅ {key}[/green]")
        else:
            console.print(f"  [yellow]⚠️  {key} not set[/yellow]")

    # Config files
    for cfg in ["config/profile.yaml", "config/preferences.yaml", "config/platforms.yaml"]:
        if (ROOT / cfg).exists():
            console.print(f"  [green]✅ {cfg}[/green]")
        else:
            console.print(f"  [red]❌ {cfg} missing[/red]")
            all_ok = False

    # Resume
    if (ROOT / "config" / "resume.pdf").exists():
        console.print("  [green]✅ config/resume.pdf[/green]")
    else:
        console.print("  [yellow]⚠️  config/resume.pdf not found — add your resume before running[/yellow]")

    # Chrome
    chrome_exe = _find_chrome()
    if chrome_exe:
        console.print(f"  [green]✅ Chrome found[/green] [dim]({chrome_exe})[/dim]")
    else:
        console.print(
            "  [red]❌ Google Chrome not found — required for login[/red]\n"
            "     https://www.google.com/chrome/"
        )
        all_ok = False

    # Enabled platforms
    platforms = _load_platforms()
    if platforms:
        console.print(f"  [green]✅ Platforms requiring login: {', '.join(platforms)}[/green]")
    else:
        console.print("  [yellow]⚠️  No platforms enabled in platforms.yaml[/yellow]")

    # Gemini connectivity
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        try:
            from google import genai  # type: ignore[import]

            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model=_GEMINI_MODEL,
                contents="Say 'ok'",
            )
            console.print(
                f"  [green]✅ Gemini API connected[/green] "
                f"[dim](model: {_GEMINI_MODEL} · response: {resp.text.strip()[:20]})[/dim]"
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]❌ Gemini API error: {exc}[/red]")
            all_ok = False

    if all_ok:
        console.print("\n[bold green]All checks passed.[/bold green] Run: python agent/main.py --dry-run")
    else:
        console.print("\n[bold yellow]Some checks failed — review above.[/bold yellow]")


def init_sheets() -> None:
    """Create the Applications and Stats worksheets with headers."""
    console.rule("[bold]Initialising Google Sheets[/bold]")
    try:
        from agent.tracker.sheets import SheetsTracker

        tracker = SheetsTracker()
        tracker.sync_stats({"total_applied": 0, "by_platform": {}, "by_status": {}})
        tracker.append_application({
            "company":     "EXAMPLE",
            "title":       "EXAMPLE ROLE",
            "url":         "https://example.com",
            "source":      "setup",
            "match_score": 0,
            "status":      "test",
            "notes":       "Created by setup.py --init-sheets — delete this row",
        })
        console.print("[green]✅ Google Sheets initialised.[/green]")
        console.print("[dim]Delete the example row in the 'Applications' tab.[/dim]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]❌ Failed: {exc}[/red]")
        console.print("[dim]Make sure GOOGLE_SHEET_ID and GOOGLE_SHEETS_CREDENTIALS_PATH are set in .env[/dim]")


def main() -> None:
    """Parse arguments and run the requested setup step."""
    platforms = _load_platforms()
    platform_names = list(platforms.keys())

    parser = argparse.ArgumentParser(description="Job Agent — one-time setup wizard")
    parser.add_argument(
        "--login",
        metavar="PLATFORM",
        help=(
            "Log in to a platform. Use 'all' for all enabled platforms, "
            f"or one of: {', '.join(platform_names) or 'none enabled'}"
        ),
    )
    parser.add_argument("--validate",    action="store_true", help="Check all API keys and configs")
    parser.add_argument("--init-sheets", action="store_true", help="Create Google Sheets tabs and headers")
    args = parser.parse_args()

    console.print(Panel.fit("[bold cyan]🔧 Job Agent Setup Wizard[/bold cyan]", border_style="cyan"))

    if args.login:
        if args.login.lower() == "all":
            selected = platforms
        elif args.login.lower() in platforms:
            selected = {args.login.lower(): platforms[args.login.lower()]}
        else:
            console.print(f"[red]❌ Unknown or disabled platform: {args.login}[/red]")
            console.print(f"   Enabled platforms: {', '.join(platform_names) or 'none'}")
            sys.exit(1)

        for name, cfg in selected.items():
            asyncio.run(login_platform(name, cfg["login_url"]))

    if args.validate:
        validate_config()

    if args.init_sheets:
        init_sheets()

    if not any([args.login, args.validate, args.init_sheets]):
        parser.print_help()


if __name__ == "__main__":
    main()
