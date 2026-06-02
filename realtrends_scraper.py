#!/usr/bin/env python3
"""
Scrape RealTrends Verified agent rankings (individuals by volume) to CSV.

Target URL:
  https://www.realtrends.com/ranking/best-real-estate-agents-united-states/individuals-by-volume/

Output columns:
  Rank, Name, Company, Volume, Sides, City, State

Usage:
  pip install -r requirements-realtrends.txt
  python realtrends_scraper.py
  python realtrends_scraper.py --output "RealTrends Verified Upwork 2026.05.31 - Sheet1.csv"
  python realtrends_scraper.py --max-rows 200   # test with first 200 agents
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

DEFAULT_URL = (
    "https://www.realtrends.com/ranking/"
    "best-real-estate-agents-united-states/individuals-by-volume/"
)
API_URL = "https://www.realtrends.com/api/trpc/submissions.getByLocation"
CSV_HEADERS = ["Rank", "Name", "Company", "Volume", "Sides", "City", "State"]
PAGE_SIZE = 50


@dataclass
class AgentRow:
    rank: str
    name: str
    company: str
    volume: str
    sides: str
    city: str
    state: str

    def as_dict(self) -> dict[str, str]:
        return {
            "Rank": self.rank,
            "Name": self.name,
            "Company": self.company,
            "Volume": self.volume,
            "Sides": self.sides,
            "City": self.city,
            "State": self.state,
        }


def build_driver(headless: bool) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    # Table is hidden below md breakpoint; force desktop layout.
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(90)
    return driver


def dismiss_overlays(driver: webdriver.Chrome) -> None:
    for selector in (
        "#onetrust-accept-btn-handler",
        "button[id*='accept']",
        "[aria-label*='Accept']",
    ):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                if el.is_displayed():
                    el.click()
                    time.sleep(0.5)
                    return
        except Exception:
            pass


def wait_for_table(driver: webdriver.Chrome, wait: WebDriverWait) -> None:
    def table_has_data(drv: webdriver.Chrome) -> bool:
        for tr in drv.find_elements(By.CSS_SELECTOR, "table tbody tr"):
            cells = tr.find_elements(By.TAG_NAME, "td")
            if len(cells) >= 5 and re.search(r"\d", cells[0].text):
                return True
        return False

    wait.until(table_has_data)


def format_volume(amount: float | int) -> str:
    value = float(amount)
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:,.0f}"


def format_sides(value: float | int | None) -> str:
    if value is None:
        return ""
    num = float(value)
    if num == int(num):
        return f"{int(num):,}"
    # Keep decimal sides exactly as reported (e.g. 28.7).
    text = f"{num:.10f}".rstrip("0").rstrip(".")
    return text


def _extract_volume_rank(rankings: dict) -> str:
    """Use national volume rank; '-' when the API does not provide it."""
    rank = rankings.get("national_rank_volume")
    if rank is not None:
        return str(rank)
    return "-"


def _api_payload(offset: int, limit: int) -> dict:
    return {
        "json": {
            "teamSize": "Individual",
            "city": None,
            "state": None,
            "borough": None,
            "year": None,
            "sortField": "volume",
            "sortDirection": "desc",
            "searchQuery": "",
            "networkAffiliation": None,
            "award": None,
            "offset": offset,
            "limit": limit,
        },
        "meta": {
            "values": {
                "city": ["undefined"],
                "state": ["undefined"],
                "borough": ["undefined"],
                "year": ["undefined"],
                "networkAffiliation": ["undefined"],
                "award": ["undefined"],
            }
        },
    }


def fetch_api_page(offset: int, limit: int, retries: int = 5) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            params = urllib.parse.urlencode(
                {"input": json.dumps(_api_payload(offset, limit), separators=(",", ":"))}
            )
            url = f"{API_URL}?{params}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Referer": DEFAULT_URL,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload["result"]["data"]["json"]
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"API request failed at offset {offset}: {last_error}") from last_error


def row_from_api_item(item: dict) -> AgentRow | None:
    name = str(item.get("name") or "").strip()
    if not name:
        return None

    rankings = item.get("rankings") or {}
    sides_raw = item.get("sides")
    return AgentRow(
        rank=_extract_volume_rank(rankings),
        name=name,
        company=str(item.get("company") or ""),
        volume=format_volume(item.get("volume") or 0),
        sides=format_sides(sides_raw),
        city=str(item.get("city") or ""),
        state=str(item.get("state") or ""),
    )


def fetch_all_via_api(max_rows: int | None = None) -> list[AgentRow]:
    rows: list[AgentRow] = []
    offset = 0
    total_count: int | None = None
    skipped_no_name = 0

    while True:
        batch = fetch_api_page(offset, PAGE_SIZE)
        if total_count is None:
            total_count = int(batch.get("count") or 0)
            print(f"Total agents reported by API: {total_count}")

        items = batch.get("data") or []
        if not items:
            if total_count and offset < total_count:
                raise RuntimeError(
                    f"Empty API page at offset {offset} but expected {total_count} total rows."
                )
            break

        for item in items:
            row = row_from_api_item(item)
            if row is None:
                skipped_no_name += 1
                continue
            rows.append(row)
            if max_rows and len(rows) >= max_rows:
                print()
                return rows[:max_rows]

        offset += len(items)
        print(f"  Fetched {len(rows)} / {total_count or '?'} rows...", end="\r")

        if total_count and offset >= total_count:
            break
        if len(items) < PAGE_SIZE and (not total_count or offset >= total_count):
            break

        time.sleep(0.15)

    print()
    if total_count and len(rows) != total_count - skipped_no_name:
        print(
            f"Warning: saved {len(rows)} rows "
            f"(API total {total_count}, skipped without name: {skipped_no_name})"
        )
    return rows


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _split_city_state(value: str) -> tuple[str, str]:
    value = _clean(value)
    if "," in value:
        city, state = [p.strip() for p in value.split(",", 1)]
        return city, state
    return value, ""


def _parse_name_company(cell_text: str, cell_element=None) -> tuple[str, str]:
    if cell_element is not None:
        parts = [
            _clean(el.text)
            for el in cell_element.find_elements(By.CSS_SELECTOR, "a, span, p, div")
            if _clean(el.text)
        ]
        if parts:
            # Usually: [name, name, company] or [name, company]
            name = parts[0]
            company = parts[-1] if len(parts) > 1 and parts[-1] != name else ""
            if len(parts) >= 3:
                company = parts[-1]
            return name, company

    text = _clean(cell_text)
    return text, ""


def extract_rows_from_dom(driver: webdriver.Chrome) -> list[AgentRow]:
    rows: list[AgentRow] = []

    for tr in driver.find_elements(By.CSS_SELECTOR, "table tbody tr"):
        cells = tr.find_elements(By.TAG_NAME, "td")
        if len(cells) < 5:
            continue

        rank = _clean(cells[0].text)
        if not rank or not re.search(r"\d", rank):
            continue

        name, company = _parse_name_company(cells[1].text, cells[1])
        volume = _clean(cells[2].text)
        sides = _clean(cells[3].text)
        city, state = _split_city_state(cells[4].text)

        rows.append(
            AgentRow(
                rank=rank,
                name=name,
                company=company,
                volume=volume,
                sides=sides,
                city=city,
                state=state,
            )
        )

    if rows:
        return _dedupe_rows(rows)

    # Mobile card fallback (visible when table is hidden on small viewports).
    for card in driver.find_elements(By.CSS_SELECTOR, "[class*='md:hidden'] > div, [class*='md:hidden'] article"):
        text = _clean(card.text)
        if not text:
            continue
        parsed = _parse_mobile_card(text)
        if parsed:
            rows.append(parsed)

    return _dedupe_rows(rows)


def _parse_mobile_card(text: str) -> AgentRow | None:
    rank_match = re.match(r"^(\d+)\s+(.+)$", text)
    if not rank_match:
        return None

    rank = rank_match.group(1)
    rest = rank_match.group(2)
    volume_match = re.search(r"Volume\s+(\$[\d.,]+[KMB]?)", rest, re.I)
    sides_match = re.search(r"Sides\s+([\d.,]+)", rest, re.I)
    if not volume_match:
        return None

    before_metrics = rest[: volume_match.start()].strip()
    city_state = before_metrics.split("|")[-1].strip() if "|" in before_metrics else ""
    city, state = _split_city_state(city_state)

    name_part = before_metrics
    if "|" in before_metrics:
        parts = [p.strip() for p in before_metrics.split("|") if p.strip()]
        name = parts[0] if parts else ""
        company = parts[1] if len(parts) > 1 else ""
    else:
        name = name_part
        company = ""

    return AgentRow(
        rank=rank,
        name=name,
        company=company,
        volume=volume_match.group(1),
        sides=sides_match.group(1) if sides_match else "",
        city=city,
        state=state,
    )


def _dedupe_rows(rows: Iterable[AgentRow]) -> list[AgentRow]:
    seen: set[tuple[str, str]] = set()
    out: list[AgentRow] = []
    for row in rows:
        key = (row.rank, row.name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=lambda r: int(re.sub(r"\D", "", r.rank) or 0))
    return out


def save_csv(rows: list[AgentRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())


def scrape(
    url: str,
    output: Path,
    headless: bool,
    wait_seconds: int,
    max_rows: int | None,
    use_api: bool,
    debug_html: Path | None,
) -> int:
    driver = build_driver(headless=headless)
    wait = WebDriverWait(driver, wait_seconds)

    try:
        print("Opening page in Chrome (Selenium)...")
        driver.get(url)
        time.sleep(2)
        dismiss_overlays(driver)

        # Page loads rankings automatically; no button click required.
        wait_for_table(driver, wait)
        print("Page loaded.")

        if debug_html:
            debug_html.write_text(driver.page_source, encoding="utf-8")

        if use_api:
            print("Fetching all agents via RealTrends API...")
            rows = fetch_all_via_api(max_rows=max_rows)
        else:
            rows = extract_rows_from_dom(driver)

        if not rows:
            raise RuntimeError("No rows extracted.")

        save_csv(rows, output)
        print(f"Saved {len(rows)} rows to {output}")
        return 0
    finally:
        driver.quit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape RealTrends agent rankings to CSV.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Ranking page URL")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("RealTrends Verified Upwork 2026.05.31 - Sheet1.csv"),
        help="Output CSV path",
    )
    parser.add_argument("--headless", action="store_true", help="Run Chrome headless")
    parser.add_argument("--wait", type=int, default=60, help="Seconds to wait for page load")
    parser.add_argument(
        "--max-rows",
        type=int,
        help="Limit rows fetched (useful for testing, e.g. --max-rows 100)",
    )
    parser.add_argument(
        "--dom-only",
        action="store_true",
        help="Only scrape visible DOM rows (default: fetch full dataset via API)",
    )
    parser.add_argument(
        "--debug-html",
        type=Path,
        help="Write page HTML after load for debugging",
    )
    args = parser.parse_args()

    try:
        return scrape(
            url=args.url,
            output=args.output,
            headless=args.headless,
            wait_seconds=args.wait,
            max_rows=args.max_rows,
            use_api=not args.dom_only,
            debug_html=args.debug_html,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
