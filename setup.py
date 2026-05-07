"""
setup.py — One-time setup wizard for the Job Application Agent.

Usage:
    python setup.py --login all          # Log in to all platforms via your real Chrome
    python setup.py --login naukri       # Log in to a specific platform
    python setup.py --validate           # Check all API keys and configs
    python setup.py --init-sheets        # Create Google Sheets tabs and headers

How login works (CDP approach):
    Chrome is launched automatically with a remote debugging port.  Playwright
    then connects to that already-running Chrome instance and navigates to the
    platform login page.  Because Chrome was not launched by Playwright, Google
    OAuth works without any blocks or "browser may not be secure" warnings.
"""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()
console = Console()

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_PLATFORMS_REQUIRING_LOGIN = ["naukri", "instahyre", "hirist", "cutshort", "foundit"]

_PLATFORM_URLS = {
    "naukri":    "https://www.naukri.com/nlogin/login",
    "instahyre": "https://www.instahyre.com/candidate/login/",
    "hirist":    "https://www.hirist.tech/login",
    "cutshort":  "https://cutshort.io/login",
    "foundit":   "https://www.foundit.in/login",
}

_CDP_PORT = 9222

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


def _find_chrome() -> str | None:
    """Return path to the Chrome executable, or None if not found."""
    for path in _CHROME_PATHS:
        if Path(path).exists():
            return path
    return None


def _launch_chrome_with_cdp(chrome_exe: str) -> None:
    """Launch Chrome with remote debugging enabled (non-blocking).

    A dedicated profile directory is used so this Chrome instance never
    conflicts with the user's normal Chrome.

    Args:
        chrome_exe: Full path to the Chrome executable.
    """
    debug_profile = ROOT / "auth" / "chrome-cdp-profile"
    debug_profile.mkdir(parents=True, exist_ok=True)

    subprocess.Popen(
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


async def login_platform(platform: str) -> None:
    """Launch Chrome via CDP, navigate to the platform login page, wait for
    manual login, then save the session cookies to disk.

    Because Playwright connects to an already-running Chrome (rather than
    launching its own), Google OAuth works without any blocks or warnings.

    Args:
        platform: Short platform name, e.g. ``"naukri"``.
    """
    from playwright.async_api import async_playwright

    from agent.submitter.session_manager import SessionManager

    url = _PLATFORM_URLS.get(platform)
    if not url:
        console.print(f"[yellow]Unknown platform: {platform}[/yellow]")
        return

    chrome_exe = _find_chrome()
    if not chrome_exe:
        console.print(
            "[red]❌ Google Chrome not found.[/red]\n"
            "   Install it from https://www.google.com/chrome/ then re-run."
        )
        return

    console.print(f"\n[bold]Logging in to {platform}[/bold]")
    console.print("[dim]Starting Chrome…[/dim]")
    _launch_chrome_with_cdp(chrome_exe)

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
            return

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        # Navigate to the login page so the user can log in straight away
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        console.print(f"[dim]Opened {url}[/dim]")
        console.print("Log in using your Google account or email/password.")

        input("\n  Press Enter once you are fully logged in… ")

        session_mgr = SessionManager()
        await session_mgr.save_session(platform, context)

        console.print(f"[green]✅ Session saved for {platform}[/green]")
        await browser.close()


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

    # Gemini connectivity
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        try:
            import google.generativeai as genai  # type: ignore[import]

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content("Say 'ok'")
            console.print(f"  [green]✅ Gemini API connected[/green] [dim]({resp.text.strip()[:30]})[/dim]")
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
    parser = argparse.ArgumentParser(description="Job Agent — one-time setup wizard")
    parser.add_argument(
        "--login",
        metavar="PLATFORM",
        help=f"Log in to a platform. Use 'all' or one of: {', '.join(_PLATFORMS_REQUIRING_LOGIN)}",
    )
    parser.add_argument("--validate",    action="store_true", help="Check all API keys and configs")
    parser.add_argument("--init-sheets", action="store_true", help="Create Google Sheets tabs and headers")
    args = parser.parse_args()

    console.print(Panel.fit("[bold cyan]🔧 Job Agent Setup Wizard[/bold cyan]", border_style="cyan"))

    if args.login:
        platforms = (
            _PLATFORMS_REQUIRING_LOGIN if args.login.lower() == "all" else [args.login.lower()]
        )
        for platform in platforms:
            asyncio.run(login_platform(platform))

    if args.validate:
        validate_config()

    if args.init_sheets:
        init_sheets()

    if not any([args.login, args.validate, args.init_sheets]):
        parser.print_help()


if __name__ == "__main__":
    main()
