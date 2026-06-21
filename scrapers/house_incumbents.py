"""
House incumbent scraper.

Pulls all 435 current House members from the @unitedstates/congress-legislators
YAML dataset (GitHub), then overlays the Ballotpedia non-running list to flag
open seats. Outputs two files:

  data/processed/house_incumbents.json   — machine-readable, used by utils/races.py
  data/processed/house_incumbents.csv    — human-readable summary

Sources:
  Current members: https://github.com/unitedstates/congress-legislators
    legislators-current.yaml (maintained by volunteers, updated daily)
  Non-running list: https://ballotpedia.org/List_of_U.S._House_incumbents_who_are_not_running_for_re-election_in_2026

Run this script whenever you want to refresh incumbent data:
  py -3 scrapers/house_incumbents.py

The output is read by utils/races.py at import time if present.
"""

import sys
import urllib.request
import re
import json
import csv
import time
from pathlib import Path
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    raise ImportError("PyYAML required: pip install pyyaml")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from utils.http_headers import DEFAULT_HEADERS

PROCESSED_DIR = ROOT / "data" / "processed"
HEADERS = DEFAULT_HEADERS

LEGISLATORS_URL = (
    "https://raw.githubusercontent.com/unitedstates/"
    "congress-legislators/main/legislators-current.yaml"
)
BALLOTPEDIA_NONRUNNING_URL = (
    "https://ballotpedia.org/List_of_U.S._House_incumbents_who_are"
    "_not_running_for_re-election_in_2026"
)

# States with a single at-large district (stored as district 0 in YAML)
AT_LARGE_STATES = {"AK", "DE", "MT", "ND", "SD", "VT", "WY", "WY"}
# Actually all single-seat states:
SINGLE_SEAT_STATES = {"AK", "DE", "ND", "SD", "VT", "WY", "MT"}


def fetch_current_members() -> dict[str, dict]:
    """
    Fetch all current House members from congress-legislators YAML.

    Returns: dict keyed by race_id "2026-H-{STATE}-{DIST:02d}"
      value: {name, party, bioguide_id}
    """
    print("Fetching congress-legislators current members YAML...")
    req = urllib.request.Request(LEGISLATORS_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = yaml.safe_load(r.read())

    house = [m for m in data if m.get("terms") and m["terms"][-1]["type"] == "rep"]
    print(f"  Found {len(house)} current House members")

    members = {}
    for m in house:
        t = m["terms"][-1]
        state = t["state"]
        district = t.get("district", 0)

        # Single-seat states store district=0; map to 01
        if district == 0:
            district = 1

        district_str = str(district).zfill(2)
        race_id = f"2026-H-{state}-{district_str}"

        party_full = t.get("party", "")
        if "Republican" in party_full:
            party = "R"
        elif "Democrat" in party_full:
            party = "D"
        else:
            party = "I"

        name = m["name"].get("official_full") or (
            f"{m['name'].get('first', '')} {m['name'].get('last', '')}".strip()
        )
        bioguide = m.get("id", {}).get("bioguide", "")

        members[race_id] = {"name": name, "party": party, "bioguide_id": bioguide}

    return members


def fetch_nonrunning_incumbents() -> dict[str, dict]:
    """
    Scrape Ballotpedia's list of House incumbents not running in 2026.

    Returns: dict keyed by incumbent name -> {state, district, reason}
    Note: matching to race_id is done by cross-referencing with members dict.
    """
    print("Fetching Ballotpedia non-running incumbents list...")
    req = urllib.request.Request(BALLOTPEDIA_NONRUNNING_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", errors="replace")

    rows = re.findall(r"<tr[^>]*>.*?</tr>", html, re.DOTALL)

    nonrunning = {}
    for row in rows[8:]:  # skip header rows
        text = re.sub(r"<[^>]+>", " ", row)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) < 15:
            continue

        party = "R" if "Republican" in row else ("D" if "Democrat" in row else None)
        if party is None:
            continue

        # Extract name from first <a> title not containing "District"
        names = re.findall(r'title="([^"]+)"', row)
        name = next(
            (n for n in names if "District" not in n and "Congressional" not in n
             and "2026" not in n and "2024" not in n and "List" not in n),
            None
        )
        if not name:
            continue

        # Determine reason: running for higher office vs retiring
        reason = "retiring"
        if "running for" in text.lower() or "senate" in text.lower():
            reason = "running-for-higher-office"
        elif "resigned" in text.lower() or "died" in text.lower():
            reason = "vacancy"

        nonrunning[name.strip()] = {"party": party, "reason": reason}

    print(f"  Found {len(nonrunning)} non-running incumbents")
    return nonrunning


def build_incumbent_data() -> list[dict]:
    """
    Combine current members + non-running list into a full 435-district dataset.
    """
    members = fetch_current_members()
    nonrunning = fetch_nonrunning_incumbents()

    # Build a name->race_id index for cross-referencing
    name_to_race = {}
    for race_id, m in members.items():
        # Use last name as key (non-running list uses various name forms)
        last = m["name"].split()[-1].lower()
        name_to_race[last] = race_id
        # Also index by full name
        name_to_race[m["name"].lower()] = race_id

    # Mark non-running incumbents
    nonrunning_race_ids = set()
    for nr_name, nr_data in nonrunning.items():
        nr_last = nr_name.split()[-1].lower()
        # Try full name match first
        matched_id = name_to_race.get(nr_name.lower()) or name_to_race.get(nr_last)
        if matched_id:
            nonrunning_race_ids.add(matched_id)
            members[matched_id]["open_seat"] = True
            members[matched_id]["open_reason"] = nr_data["reason"]

    rows = []
    for race_id, m in sorted(members.items()):
        rows.append({
            "race_id": race_id,
            "incumbent_name": m["name"],
            "incumbent_party": m["party"],
            "bioguide_id": m.get("bioguide_id", ""),
            "open_seat": m.get("open_seat", False),
            "open_reason": m.get("open_reason", ""),
        })

    open_count = sum(1 for r in rows if r["open_seat"])
    print(f"  Total districts: {len(rows)}, open seats: {open_count}")
    return rows


def run():
    """Fetch and save House incumbent data."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    rows = build_incumbent_data()

    # Save JSON
    json_path = PROCESSED_DIR / "house_incumbents.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {"fetched_at": datetime.now(timezone.utc).isoformat(), "members": rows},
            f, indent=2, ensure_ascii=False
        )
    print(f"Saved JSON -> {json_path}")

    # Save CSV
    csv_path = PROCESSED_DIR / "house_incumbents.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved CSV  -> {csv_path}")

    # Summary
    r_count = sum(1 for r in rows if r["incumbent_party"] == "R")
    d_count = sum(1 for r in rows if r["incumbent_party"] == "D")
    open_count = sum(1 for r in rows if r["open_seat"])
    print(f"\nSummary: R={r_count}, D={d_count}, Open={open_count}")
    print("\nOpen seats:")
    for r in rows:
        if r["open_seat"]:
            print(f"  {r['race_id']:22} {r['incumbent_party']} {r['incumbent_name']:35} ({r['open_reason']})")


if __name__ == "__main__":
    run()
