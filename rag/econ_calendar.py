"""US macro-release calendar: FOMC meeting dates, BEA GDP/PCE dates, and BLS
CPI/NFP/PPI dates.

FOMC (federalreserve.gov) and BEA (bea.gov) are plain script-fetchable and
refreshed live on every call. BLS (bls.gov) blocks scripted requests --
confirmed 403 on /schedule/news_release/{cpi,empsit,ppi}.htm regardless of
User-Agent/Accept headers -- so those dates are a manually-seeded list below.
Refresh BLS_SEED by asking Claude to re-fetch those three URLs (it has fetch
access this script doesn't) and updating the entries; BLS publishes each
year's full schedule well in advance, so this needs only occasional review,
not a daily touch.

Stored as plain JSON (rag/corpus/calendar/events.json), not embedded into
FAISS -- these are exact-date lookups, not similarity-search content, so a
vector index would be the wrong tool.

Run:  python -m rag.econ_calendar     (refresh + print the next 30 days)
      python -m rag.update            (also refreshes this, alongside news)
"""

import json
import re
import urllib.request
from datetime import date, timedelta

from config import RAG_CORPUS_DIR

CALENDAR_FILE = RAG_CORPUS_DIR / "calendar" / "events.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BEA_URL = "https://www.bea.gov/news/schedule"

# Seeded from a live fetch of bls.gov/schedule/news_release/{cpi,empsit,ppi}.htm
# on 2026-09-02 -- BLS blocks scripted fetches, so this can't self-refresh.
BLS_SEED = [
    {"event": "CPI", "reference_period": "August 2026",
     "date": "2026-09-11", "source": "BLS"},
    {"event": "CPI", "reference_period": "September 2026",
     "date": "2026-10-14", "source": "BLS"},
    {"event": "CPI", "reference_period": "October 2026",
     "date": "2026-11-10", "source": "BLS"},
    {"event": "CPI", "reference_period": "November 2026",
     "date": "2026-12-10", "source": "BLS"},
    {"event": "Nonfarm Payrolls", "reference_period": "August 2026",
     "date": "2026-09-04", "source": "BLS"},
    {"event": "Nonfarm Payrolls", "reference_period": "September 2026",
     "date": "2026-10-02", "source": "BLS"},
    {"event": "Nonfarm Payrolls", "reference_period": "October 2026",
     "date": "2026-11-06", "source": "BLS"},
    {"event": "Nonfarm Payrolls", "reference_period": "November 2026",
     "date": "2026-12-04", "source": "BLS"},
    {"event": "PPI", "reference_period": "September 2026",
     "date": "2026-10-15", "source": "BLS"},
    {"event": "PPI", "reference_period": "October 2026",
     "date": "2026-11-13", "source": "BLS"},
    {"event": "PPI", "reference_period": "November 2026",
     "date": "2026-12-15", "source": "BLS"},
]

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_fomc() -> list[dict]:
    """FOMC meeting decision dates (2nd day of each 2-day meeting)."""
    html = _fetch(FOMC_URL)
    today = date.today()
    year_markers = list(re.finditer(r'<a id="\d+">(\d{4}) FOMC Meetings</a>', html))
    events = []
    for i, ym in enumerate(year_markers):
        year = int(ym.group(1))
        block_end = year_markers[i + 1].start() if i + 1 < len(year_markers) else len(html)
        block = html[ym.end():block_end]
        for m in re.finditer(
            r'fomc-meeting__month[^"]*"><strong>(\w+)</strong></div>.*?'
            r'fomc-meeting__date[^"]*">([^<]+)</div>', block, re.S):
            month_name, day_text = m.group(1), m.group(2)
            if month_name not in MONTHS:
                continue
            digits = re.findall(r"\d+", day_text)
            if not digits:
                continue
            day = int(digits[-1])  # decision day = 2nd day of the meeting
            try:
                d = date(year, MONTHS[month_name], day)
            except ValueError:
                continue
            if d < today:
                continue
            events.append({"event": "FOMC Meeting (rate decision)",
                           "reference_period": f"{month_name} {year}",
                           "date": d.isoformat(), "source": "Federal Reserve"})
    return events


def fetch_bea() -> list[dict]:
    """National GDP estimates and Personal Income/Outlays (PCE) releases."""
    html = _fetch(BEA_URL)
    today = date.today()
    year, last_month = today.year, 0
    events = []
    for m in re.finditer(
        r'<div class="release-date">(\w+) (\d{1,2})</div>.*?'
        r'release-title[^>]*>([^<]+?)\s*</td>', html, re.S):
        month_name, day, title = m.group(1), int(m.group(2)), m.group(3).strip()
        if month_name not in MONTHS:
            continue
        if not (title.startswith("GDP (") or
                title.startswith("Personal Income and Outlays")):
            continue
        month = MONTHS[month_name]
        if month < last_month:  # rolled into next year
            year += 1
        last_month = month
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d < today:
            continue
        label = ("GDP" if title.startswith("GDP (")
                 else "Personal Income & Outlays (PCE)")
        events.append({"event": label, "reference_period": title,
                       "date": d.isoformat(), "source": "BEA"})
    return events


def refresh() -> list[dict]:
    """Fetch FOMC + BEA live, merge with the BLS seed, dedupe, save."""
    events = fetch_fomc() + fetch_bea() + BLS_SEED
    seen: set[tuple[str, str]] = set()
    deduped = []
    for e in sorted(events, key=lambda e: e["date"]):
        key = (e["event"], e["date"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_FILE.write_text(json.dumps(deduped, indent=2), encoding="utf-8")
    return deduped


def upcoming(days: int = 14, today: str | None = None) -> list[dict]:
    """Events within the next `days` days of `today` (default: today)."""
    if not CALENDAR_FILE.exists():
        refresh()
    events = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    ref = date.fromisoformat(today) if today else date.today()
    horizon = (ref + timedelta(days=days)).isoformat()
    return [e for e in events if ref.isoformat() <= e["date"] <= horizon]


if __name__ == "__main__":
    saved = refresh()
    print(f"Calendar refreshed: {len(saved)} events saved to {CALENDAR_FILE}")
    print()
    for e in upcoming(30):
        print(f"{e['date']}  {e['event']:<32} ({e['reference_period']}, "
              f"{e['source']})")
