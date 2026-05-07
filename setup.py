"""
setup.py — One-time setup wizard for the Job Application Agent.

Usage:
    python setup.py --login all          # Open browser for all platforms
    python setup.py --login naukri       # Open browser for a specific platform
    python setup.py --validate           # Check all API keys and configs
    python setup.py --init-sheets        # Create Google Sheets tabs and headers
"""

import argparse
import asyncio
import os
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
    "naukri": "https://www.naukri.com/nlogin/login",
    "instahyre": "https://www.instahyre.com/candidate/login/",
    "hirist": "https://www.hirist.tech/login",
    "cutshort": "https://cutshort.io/login",
    "foundit": "https://www.foundit.in/login",
}

# Common Chrome installation paths per OS
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
    """Return the path to the user's real Chrome/Chromium installation, or None."""
    for path in _CHROME_PATHS:
        if Path(path).exists():
            return path
    return None


async def login_platform(platform: str) -> None:
    """Open a headed browser for manual login, then save the session.

    Uses the user's real Chrome installation so Google OAuth is not blocked.
    Falls back to Playwright's bundled Chromium if Chrome is not found.

    Args:
        platform: Platform name, e.g. "naukri".
    """
    from playwright.async_api import async_playwright

    from agent.submitter.session_manager import SessionManager

    url = _PLATFORM_URLS.get(platform)
    if not url:
        console.print(f"  [yellow]Unknown platform: {platform}[/yellow]")
        return

    chrome_path = _find_chrome()
    if chrome_path:
        console.print(f"  [dim]Using real Chrome at: {chrome_path}[/dim]")
    else:
        console.print(
            "  [yellow]⚠️  Real Chrome not found — falling back to Playwright Chromium.[/yellow]\n"
            "  [yellow]   If Google OAuth login is blocked, install Google Chrome and retry.[/yellow]"
        )

    console.print(f"\n  Opening browser for [bold]{platform}[/bold]…")
    console.print(f"  URL: {url}")
    console.print("  [dim]Log in manually in the browser window, then come back here and press Enter.[/dim]")

    async with async_playwright() as pw:
        launch_kwargs: dict = {
            "headless": False,
            # Slow down actions slightly so pages load before we interact
            "slow_mo": 100,
            # Pass args that make Chrome behave more like a real user session
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        }

        if chrome_path:
            # Use the real Chrome executable — Google won't block this
            launch_kwargs["executable_path"] = chrome_path
            browser = await pw.chromium.launch(**launch_kwargs)
        else:
            browser = await pw.chromium.launch(**launch_kwargs)

        # Create a context that looks like a real user (not a bot)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
        )

        page = await context.new_page()

        # Hide the webdriver flag that sites use to detect automation
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        console.print("\n  Waiting for you to log in…  (Press Enter when done) ", end="")
        input()

        session_mgr = SessionManager()
        await session_mgr.save_session(platform, context)
        console.print(f"  [green]✅ Session saved for {platform}[/green]")

        await context.close()
        await browser.close()


def validate_config() -> None:
    """Check that all required API keys and config files are present."""
    console.rule("[bold green]Validating configuration[/bold green]")
    all_ok = True

    # Check .env / environment
    checks = {
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "GOOGLE_SHEET_ID": os.getenv("GOOGLE_SHEET_ID"),
        "SLACK_WEBHOOK_URL": os.getenv("SLACK_WEBHOOK_URL"),
    }
    for key, val in checks.items():
        if val and val not in ("your_gemini_api_key_here", "your_google_sheet_id_here"):
            console.print(f"  [green]✅ {key}[/green]")
        else:
            console.print(f"  [yellow]⚠️  {key} not set (optional but recommended)[/yellow]")

    # Check config files
    for cfg in ["config/profile.yaml", "config/preferences.yaml", "config/platforms.yaml"]:
        path = ROOT / cfg
        if path.exists():
            console.print(f"  [green]✅ {cfg}[/green]")
        else:
            console.print(f"  [red]❌ {cfg} missing[/red]")
            all_ok = False

    # Check resume
    resume_path = ROOT / "config" / "resume.pdf"
    if resume_path.exists():
        console.print(f"  [green]✅ config/resume.pdf found[/green]")
    else:
        console.print("  [yellow]⚠️  config/resume.pdf not found — add your resume before running[/yellow]")

    # Check Chrome installation
    chrome_path = _find_chrome()
    if chrome_path:
        console.print(f"  [green]✅ Chrome found at: {chrome_path}[/green]")
    else:
        console.print(
            "  [yellow]⚠️  Google Chrome not found. Install it for reliable login on all platforms.[/yellow]\n"
            "     Download: https://www.google.com/chrome/"
        )

    # Test Gemini connectivity
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        try:
            import google.generativeai as genai  # type: ignore[import]

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content("Say 'ok'")
            console.print(f"  [green]✅ Gemini API connected — response: {response.text.strip()[:30]}[/green]")
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]❌ Gemini API error: {exc}[/red]")
            all_ok = False

    if all_ok:
        console.print("\n  [bold green]All checks passed! Run: python agent/main.py --dry-run[/bold green]")
    else:
        console.print("\n  [bold yellow]Some checks failed. Review the issues above.[/bold yellow]")


def init_sheets() -> None:
    """Create the Applications and Stats worksheets with headers."""
    console.rule("[bold green]Initialising Google Sheets[/bold green]")
    try:
        from agent.tracker.sheets import SheetsTracker

        tracker = SheetsTracker()
        # Trigger sheet creation by calling sync_stats with empty stats
        tracker.sync_stats({"total_applied": 0, "by_platform": {}, "by_status": {}})
        tracker.append_application({
            "company": "EXAMPLE",
            "title": "EXAMPLE ROLE",
            "url": "https://example.com",
            "source": "setup",
            "match_score": 0,
            "status": "test",
            "notes": "Created by setup.py --init-sheets — delete this row",
        })
        console.print("  [green]✅ Google Sheets initialised.[/green]")
        console.print("  Open your spreadsheet and delete the example row in 'Applications'.")
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [red]❌ Failed to initialise sheets: {exc}[/red]")
        console.print("  Make sure GOOGLE_SHEET_ID and GOOGLE_SHEETS_CREDENTIALS_PATH are set in .env")


def main() -> None:
    """Parse arguments and run the requested setup step."""
    parser = argparse.ArgumentParser(description="Job Agent — one-time setup wizard")
    parser.add_argument(
        "--login",
        metavar="PLATFORM",
        help=f"Open browser to login. Use 'all' or one of: {', '.join(_PLATFORMS_REQUIRING_LOGIN)}",
    )
    parser.add_argument("--validate", action="store_true", help="Check all API keys and configs")
    parser.add_argument("--init-sheets", action="store_true", help="Create Google Sheets tabs and headers")
    args = parser.parse_args()

    console.print(
        Panel.fit(
            "[bold cyan]🔧 Job Agent Setup Wizard[/bold cyan]",
            border_style="cyan",
        )
    )

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
