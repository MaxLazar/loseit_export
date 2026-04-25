#!/usr/bin/env python3.12
"""
Lose It! nutrition data exporter.
Downloads the full export archive, extracts food-logs.csv, and filters by date range.
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import urllib.parse
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

LOGIN_URL = "https://my.loseit.com/login"
EXPORT_URL = "https://www.loseit.com/export/data"
INTERVALS_BASE = "https://intervals.icu/api/v1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------

def get_date_range(days: int | None = None) -> tuple[datetime, datetime]:
    days = days or int(os.getenv("DAYS_RANGE", "1"))
    end = datetime.now().date()
    start = end - timedelta(days=days - 1)
    return (
        datetime(start.year, start.month, start.day, 0, 0, 0),
        datetime(end.year, end.month, end.day, 23, 59, 59),
    )


# ---------------------------------------------------------------------------
# Lose It! authentication (Playwright handles JS-based login)
# ---------------------------------------------------------------------------

def authenticate(session: requests.Session, email: str, password: str, debug: bool = False) -> None:
    """
    Uses a headless browser to complete the JS-based login, then copies
    the resulting cookies into the requests session for the export download.
    We redirect to the dashboard (not the export URL) to avoid the browser
    treating the export zip as a navigation and raising ERR_ABORTED.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    dashboard_url = "https://www.loseit.com/"
    login_url = f"{LOGIN_URL}?r={urllib.parse.quote(dashboard_url)}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not debug)
        ctx = browser.new_context()
        page = ctx.new_page()

        page.goto(login_url, wait_until="domcontentloaded", timeout=30_000)

        # Email field
        page.locator(
            'input[type="email"], input[name="email"], input[placeholder*="email" i]'
        ).first.fill(email)

        # Password field
        page.locator('input[type="password"]').first.fill(password)

        # Submit — wrap in expect_navigation to cleanly track the redirect to dashboard
        try:
            with page.expect_navigation(timeout=20_000):
                page.locator('input[type="password"]').first.press("Enter")
        except PWTimeout:
            raise SystemExit("Login timed out — check LOSEIT_EMAIL and LOSEIT_PASSWORD.")

        page.wait_for_load_state("networkidle", timeout=20_000)

        if "login" in page.url.lower():
            raise SystemExit("Login failed — check LOSEIT_EMAIL and LOSEIT_PASSWORD.")

        # Transfer all cookies (across .loseit.com, my.loseit.com, www.loseit.com)
        for c in ctx.cookies():
            session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", "").lstrip(".")
            )

        browser.close()


def download_export(session: requests.Session) -> bytes:
    resp = session.get(EXPORT_URL, timeout=120, stream=True)
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "zip" not in content_type and "octet-stream" not in content_type:
        # The export page might redirect to a download link — follow it
        if resp.history:
            final_url = resp.url
            resp = session.get(final_url, timeout=120)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")

        if "zip" not in content_type and "octet-stream" not in content_type:
            raise SystemExit(
                f"Export did not return a zip file (content-type: {content_type!r}).\n"
                "The site may have changed or authentication failed."
            )

    return resp.content


# ---------------------------------------------------------------------------
# CSV processing
# ---------------------------------------------------------------------------

def process_export(zip_data: bytes, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        csv_name = next(
            (n for n in zf.namelist() if "food-log" in n.lower() and n.endswith(".csv")),
            None,
        )
        if csv_name is None:
            raise SystemExit(
                f"food-logs.csv not found in archive. Contents: {zf.namelist()}"
            )
        with zf.open(csv_name) as f:
            df = pd.read_csv(f)

    date_col = next(
        (c for c in df.columns if "date" in c.lower()),
        None,
    )
    if date_col is None:
        raise SystemExit(f"No date column found. Columns: {list(df.columns)}")

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    mask = (df[date_col] >= start_date) & (df[date_col] <= end_date)
    return df[mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def save_report(df: pd.DataFrame, start: datetime, end: datetime, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}_nutrition.csv"
    path = output_dir / fname
    df.to_csv(path, index=False)
    return path


def _calorie_col(df: pd.DataFrame) -> str | None:
    return next((c for c in df.columns if "calorie" in c.lower()), None)


def write_github_summary(df: pd.DataFrame, start: datetime, end: datetime) -> None:
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    cal_col = _calorie_col(df)
    date_col = next((c for c in df.columns if "date" in c.lower()), None)
    unique_days = df[date_col].dt.date.nunique() if date_col else 1

    lines = [
        "## Lose It! Nutrition Report",
        f"**Period:** {start.date()} → {end.date()}  ",
        f"**Entries logged:** {len(df)}  ",
    ]
    if cal_col:
        total_cal = df[cal_col].sum()
        lines.append(f"**Total calories:** {total_cal:,.0f}  ")
        if unique_days:
            lines.append(f"**Daily average:** {total_cal / unique_days:,.0f} kcal  ")
    lines += ["", "```", df.to_string(index=False), "```", ""]

    with open(summary_file, "a") as f:
        f.write("\n".join(lines))


def print_summary(df: pd.DataFrame, output_path: Path | None = None) -> None:
    cal_col = _calorie_col(df)
    date_col = next((c for c in df.columns if "date" in c.lower()), None)
    unique_days = df[date_col].dt.date.nunique() if date_col else 1

    print(f"\nEntries: {len(df)}")
    if cal_col:
        total = df[cal_col].sum()
        print(f"Total calories : {total:,.0f}")
        if unique_days:
            print(f"Daily average  : {total / unique_days:,.0f} kcal")
    if output_path:
        print(f"Saved          : {output_path}")


# ---------------------------------------------------------------------------
# Intervals.icu integration
# ---------------------------------------------------------------------------

def post_to_intervals(df: pd.DataFrame, date: datetime, athlete_id: str, api_key: str) -> None:
    """Create or update a 'Nutrition' NOTE event in intervals.icu for the given date."""
    auth = ("API_KEY", api_key)
    date_str = date.strftime("%Y-%m-%d")
    events_url = f"{INTERVALS_BASE}/athlete/{athlete_id}/events"

    # Find existing 'Nutrition' note for this date
    resp = requests.get(
        events_url,
        auth=auth,
        params={"oldest": date_str, "newest": date_str},
        timeout=30,
    )
    print(f"  GET {resp.url} → {resp.status_code}")
    if not resp.ok:
        print(f"  Response: {resp.text[:500]}")
        resp.raise_for_status()

    events = resp.json()
    event_labels = [f"{e.get('category')} / {e.get('name')}" for e in events]
    print(f"  Events on {date_str}: {event_labels}")

    existing = next(
        (e for e in events if e.get("category") == "NOTE" and e.get("name") == "Nutrition"),
        None,
    )

    payload = {
        "start_date_local": f"{date_str}T00:00:00",
        "category": "NOTE",
        "name": "Nutrition",
        "description": df.to_csv(index=False),
    }

    if existing:
        resp = requests.put(f"{events_url}/{existing['id']}", auth=auth, json=payload, timeout=30)
        action = "updated"
    else:
        resp = requests.post(events_url, auth=auth, json=payload, timeout=30)
        action = "created"

    print(f"  {action.upper()} → {resp.status_code}")
    if not resp.ok:
        print(f"  Response: {resp.text[:500]}")
        resp.raise_for_status()

    print(f"Intervals.icu note {action} for {date_str}")


# ---------------------------------------------------------------------------
# GitHub Actions deploy helper
# ---------------------------------------------------------------------------

def deploy_to_github(env_path: Path) -> None:
    load_dotenv(env_path, override=True)

    email = os.getenv("LOSEIT_EMAIL")
    password = os.getenv("LOSEIT_PASSWORD")
    repo = os.getenv("GITHUB_REPO")  # owner/repo
    days_range = os.getenv("DAYS_RANGE", "1")
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
    api_key = os.getenv("INTERVALS_API_KEY")

    missing = [k for k, v in {
        "LOSEIT_EMAIL": email,
        "LOSEIT_PASSWORD": password,
        "GITHUB_REPO": repo,
        "INTERVALS_ATHLETE_ID": athlete_id,
        "INTERVALS_API_KEY": api_key,
    }.items() if not v]
    if missing:
        raise SystemExit(f"Missing required .env variables for --deploy: {', '.join(missing)}")

    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit("GitHub CLI (gh) not found. Install from: https://cli.github.com/")

    auth_check = subprocess.run(["gh", "auth", "status"], capture_output=True)
    if auth_check.returncode != 0:
        raise SystemExit(
            "GitHub CLI is not authenticated.\n"
            "Run:  gh auth login\n"
            "Then re-run:  python3.12 export_loseit.py --deploy"
        )

    print(f"Configuring GitHub Actions for: {repo}")

    for secret_name, secret_value in [
        ("LOSEIT_EMAIL", email),
        ("LOSEIT_PASSWORD", password),
        ("INTERVALS_ATHLETE_ID", athlete_id),
        ("INTERVALS_API_KEY", api_key),
    ]:
        print(f"  Setting secret {secret_name}...")
        result = subprocess.run(
            ["gh", "secret", "set", secret_name, "--body", secret_value, "--repo", repo],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise SystemExit(f"Failed to set {secret_name}: {result.stderr.strip()}")

    print(f"  Setting variable DAYS_RANGE={days_range}...")
    result = subprocess.run(
        ["gh", "variable", "set", "DAYS_RANGE", "--body", days_range, "--repo", repo],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"Failed to set DAYS_RANGE variable: {result.stderr.strip()}")

    print("\nDone! Secrets and variables are configured.")
    print(f"Workflow: https://github.com/{repo}/actions")
    print("Push the .github/workflows/loseit_export.yml file to activate the schedule.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Lose It! nutrition data filtered by date range."
    )
    parser.add_argument("--days", type=int, help="Number of days to include (default: 1 or DAYS_RANGE env var)")
    parser.add_argument("--from-date", metavar="YYYY-MM-DD", help="Start date (overrides --days)")
    parser.add_argument("--to-date", metavar="YYYY-MM-DD", help="End date (overrides --days)")
    parser.add_argument("--output", metavar="DIR", help="Output directory (default: reports/)")
    parser.add_argument("--github-summary", action="store_true", help="Write output to GITHUB_STEP_SUMMARY")
    parser.add_argument("--deploy", action="store_true", help="Push secrets/variables to GitHub Actions from .env")
    parser.add_argument("--intervals", action=argparse.BooleanOptionalAction, default=True,
                        help="Upload to intervals.icu (default: true, disable with --no-intervals)")
    parser.add_argument("--debug", action="store_true", help="Show browser window during login (for troubleshooting)")
    args = parser.parse_args()

    if args.deploy:
        deploy_to_github(Path(".env"))
        return

    email = os.getenv("LOSEIT_EMAIL")
    password = os.getenv("LOSEIT_PASSWORD")
    if not email or not password:
        raise SystemExit("LOSEIT_EMAIL and LOSEIT_PASSWORD must be set (in .env or environment).")

    if args.from_date and args.to_date:
        start_date = datetime.strptime(args.from_date, "%Y-%m-%d")
        end_date = datetime.strptime(args.to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    else:
        start_date, end_date = get_date_range(args.days)

    print(f"Date range : {start_date.date()} → {end_date.date()}")
    print("Authenticating with Lose It!...")
    session = requests.Session()
    session.headers.update(HEADERS)
    authenticate(session, email, password, debug=args.debug)

    print("Downloading export archive...")
    zip_data = download_export(session)

    print("Processing data...")
    df = process_export(zip_data, start_date, end_date)

    if df.empty:
        print("No entries found for the selected date range.")
        sys.exit(0)

    if args.github_summary:
        write_github_summary(df, start_date, end_date)
        print_summary(df)
    else:
        output_dir = Path(args.output) if args.output else Path("reports")
        output_path = save_report(df, start_date, end_date, output_dir)
        print_summary(df, output_path)

    # Post to intervals.icu if enabled and credentials are present
    if args.intervals:
        athlete_id = os.getenv("INTERVALS_ATHLETE_ID")
        api_key = os.getenv("INTERVALS_API_KEY")
        if athlete_id and api_key:
            print("Posting to intervals.icu...")
            post_to_intervals(df, end_date, athlete_id, api_key)


if __name__ == "__main__":
    main()
