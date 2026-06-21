"""
Canonical race list for the 2026 election cycle.

Race ID format: {year}-{office}-{state_abbrev}[-{district}]
  Examples:
    2026-SEN-PA          Pennsylvania Senate (regular)
    2026-SEN-FL-S        Florida Senate (special, Rubio vacancy)
    2026-SEN-OH-S        Ohio Senate (special, Vance vacancy)
    2026-GOV-TX          Texas Governor
    2026-H-PA-07         Pennsylvania's 7th Congressional District

Sources (verified 2026-04-07):
  Senate: https://en.wikipedia.org/wiki/2026_United_States_Senate_elections
          https://ballotpedia.org/United_States_Senate_elections,_2026
  Governor: https://en.wikipedia.org/wiki/2026_United_States_gubernatorial_elections
            https://ballotpedia.org/Gubernatorial_elections,_2026
  House: https://ballotpedia.org/United_States_House_of_Representatives_elections,_2026
         incumbents populated via scrapers/ballotpedia.py (run to refresh)

TODO: Add cook_rating, sabato_rating, cpvi fields once ratings are published.
TODO: House incumbent_name/party populated via scrapers/ballotpedia.py — run it.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

STATE_ABBREVS = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}


@dataclass
class Race:
    race_id: str
    year: int
    office: str               # SEN, GOV, H
    state: str                # full state name
    state_abbrev: str
    district: Optional[str] = None          # House only, zero-padded e.g. "07"
    incumbent_party: Optional[str] = None   # D, R, I, or None if open/special
    incumbent_name: Optional[str] = None    # None if open seat
    open_seat: bool = False                 # True if incumbent not running
    open_reason: Optional[str] = None       # "retiring", "term-limited", "running-for-senate", etc.
    special: bool = False                   # True if special election
    primary_date: Optional[str] = None      # ISO date of primary, e.g. "2026-05-19"
    general_date: str = "2026-11-03"        # all regular races on this date
    # TODO: add cook_rating, sabato_rating, cpvi


# ---------------------------------------------------------------------------
# 2026 Senate races — 33 regular (Class 2) + 2 special elections
#
# Special elections:
#   FL: Marco Rubio resigned to become Secretary of State (Jan 2025)
#       Special election for remaining term (expires Jan 2027)
#   OH: JD Vance resigned to become VP (Jan 2025)
#       Special election for remaining term (expires Jan 2029)
#
# Incumbents NOT running for re-election (open seats):
#   AL: Tommy Tuberville — running for Governor
#   IL: Dick Durbin — retiring
#   IA: Joni Ernst — retiring
#   KY: Mitch McConnell — retiring
#   MI: Gary Peters — retiring
#   MN: Tina Smith — retiring
#   MT: Steve Daines — retiring
#   NC: Thom Tillis — retiring
#   NH: Jeanne Shaheen — retiring
#   WA: Patty Murray — retiring
#   WY: Cynthia Lummis — retiring
#
# Corrections from initial stub:
#   KS: Jerry Moran -> Roger Marshall (Moran retired in 2022, Marshall holds the seat)
#   ID: Mike Crapo -> Jim Risch (Risch is Class II, Crapo is Class III)
#   MS: Roger Wicker -> Cindy Hyde-Smith (Wicker is Class I, Hyde-Smith is Class II)
#   NE: Deb Fischer -> Pete Ricketts (Fischer is Class III, Ricketts holds Class II vacancy)
#   OK: James Lankford -> Markwayne Mullin (Lankford is Class III, Mullin is Class II)
#   SD: John Thune -> Mike Rounds (Thune is Class III, Rounds is Class II)
#   TN: Marsha Blackburn -> Bill Hagerty (Blackburn is Class I, Hagerty is Class II)
#   WV: no Class II seat — Shelley Moore Capito is Class II; added
#   MA: Ed Markey added (not in original stub)
#   WA: Patty Murray corrected — retiring (not simply "retiring" tagged, open_seat=True)
# ---------------------------------------------------------------------------
SENATE_RACES_2026 = [
    # Alabama — open seat (Tuberville running for Governor)
    Race("2026-SEN-AL", 2026, "SEN", "Alabama", "AL",
         incumbent_party="R", incumbent_name="Tommy Tuberville",
         open_seat=True, open_reason="running-for-governor",
         primary_date="2026-05-19"),

    # Alaska — incumbent running
    Race("2026-SEN-AK", 2026, "SEN", "Alaska", "AK",
         incumbent_party="R", incumbent_name="Dan Sullivan",
         primary_date="2026-08-18"),

    # Arkansas — incumbent running
    Race("2026-SEN-AR", 2026, "SEN", "Arkansas", "AR",
         incumbent_party="R", incumbent_name="Tom Cotton",
         primary_date="2026-03-03"),

    # Colorado — incumbent running
    Race("2026-SEN-CO", 2026, "SEN", "Colorado", "CO",
         incumbent_party="D", incumbent_name="John Hickenlooper",
         primary_date="2026-06-30"),

    # Delaware — incumbent running
    Race("2026-SEN-DE", 2026, "SEN", "Delaware", "DE",
         incumbent_party="D", incumbent_name="Chris Coons",
         primary_date="2026-09-15"),

    # Florida — SPECIAL ELECTION (Rubio resigned Jan 2025)
    Race("2026-SEN-FL-S", 2026, "SEN", "Florida", "FL",
         incumbent_party="R", incumbent_name=None,
         open_seat=True, open_reason="rubio-resigned-secretary-of-state",
         special=True, primary_date="2026-08-18",
         general_date="2026-11-03"),

    # Georgia — incumbent running
    Race("2026-SEN-GA", 2026, "SEN", "Georgia", "GA",
         incumbent_party="D", incumbent_name="Jon Ossoff",
         primary_date="2026-05-19"),

    # Hawaii — incumbent running
    Race("2026-SEN-HI", 2026, "SEN", "Hawaii", "HI",
         incumbent_party="D", incumbent_name="Brian Schatz",
         primary_date="2026-08-08"),

    # Idaho — incumbent running (Jim Risch, Class II)
    Race("2026-SEN-ID", 2026, "SEN", "Idaho", "ID",
         incumbent_party="R", incumbent_name="Jim Risch",
         primary_date="2026-05-19"),

    # Illinois — open seat (Durbin retiring)
    Race("2026-SEN-IL", 2026, "SEN", "Illinois", "IL",
         incumbent_party="D", incumbent_name="Dick Durbin",
         open_seat=True, open_reason="retiring",
         primary_date="2026-03-17"),

    # Iowa — open seat (Ernst retiring)
    Race("2026-SEN-IA", 2026, "SEN", "Iowa", "IA",
         incumbent_party="R", incumbent_name="Joni Ernst",
         open_seat=True, open_reason="retiring",
         primary_date="2026-06-02"),

    # Kansas — incumbent running (Roger Marshall, Class II)
    Race("2026-SEN-KS", 2026, "SEN", "Kansas", "KS",
         incumbent_party="R", incumbent_name="Roger Marshall",
         primary_date="2026-08-04"),

    # Kentucky — open seat (McConnell retiring)
    Race("2026-SEN-KY", 2026, "SEN", "Kentucky", "KY",
         incumbent_party="R", incumbent_name="Mitch McConnell",
         open_seat=True, open_reason="retiring",
         primary_date="2026-05-19"),

    # Louisiana — incumbent running
    Race("2026-SEN-LA", 2026, "SEN", "Louisiana", "LA",
         incumbent_party="R", incumbent_name="Bill Cassidy",
         primary_date="2026-05-16"),

    # Maine — incumbent running
    Race("2026-SEN-ME", 2026, "SEN", "Maine", "ME",
         incumbent_party="R", incumbent_name="Susan Collins",
         primary_date="2026-06-09"),

    # Massachusetts — incumbent running (Ed Markey, Class II)
    Race("2026-SEN-MA", 2026, "SEN", "Massachusetts", "MA",
         incumbent_party="D", incumbent_name="Ed Markey",
         primary_date="2026-09-01"),

    # Michigan — open seat (Peters retiring)
    Race("2026-SEN-MI", 2026, "SEN", "Michigan", "MI",
         incumbent_party="D", incumbent_name="Gary Peters",
         open_seat=True, open_reason="retiring",
         primary_date="2026-08-04"),

    # Minnesota — open seat (Tina Smith retiring)
    Race("2026-SEN-MN", 2026, "SEN", "Minnesota", "MN",
         incumbent_party="D", incumbent_name="Tina Smith",
         open_seat=True, open_reason="retiring",
         primary_date="2026-08-11"),

    # Mississippi — incumbent running (Cindy Hyde-Smith, Class II)
    Race("2026-SEN-MS", 2026, "SEN", "Mississippi", "MS",
         incumbent_party="R", incumbent_name="Cindy Hyde-Smith",
         primary_date="2026-03-10"),

    # Montana — open seat (Daines retiring)
    Race("2026-SEN-MT", 2026, "SEN", "Montana", "MT",
         incumbent_party="R", incumbent_name="Steve Daines",
         open_seat=True, open_reason="retiring",
         primary_date="2026-06-02"),

    # Nebraska — incumbent running (Pete Ricketts, Class II)
    Race("2026-SEN-NE", 2026, "SEN", "Nebraska", "NE",
         incumbent_party="R", incumbent_name="Pete Ricketts",
         primary_date="2026-05-12"),

    # Nevada — no Class II seat (no race in 2026)
    # Nevada's senators are Class III (Cortez Masto) and Class I (Rosen) — neither up in 2026

    # New Hampshire — open seat (Shaheen retiring)
    Race("2026-SEN-NH", 2026, "SEN", "New Hampshire", "NH",
         incumbent_party="D", incumbent_name="Jeanne Shaheen",
         open_seat=True, open_reason="retiring",
         primary_date="2026-09-08"),

    # New Jersey — incumbent running
    Race("2026-SEN-NJ", 2026, "SEN", "New Jersey", "NJ",
         incumbent_party="D", incumbent_name="Cory Booker",
         primary_date="2026-06-02"),

    # New Mexico — incumbent running (Ben Ray Luján, Class II)
    Race("2026-SEN-NM", 2026, "SEN", "New Mexico", "NM",
         incumbent_party="D", incumbent_name="Ben Ray Luján",
         primary_date="2026-06-02"),

    # North Carolina — open seat (Tillis retiring)
    Race("2026-SEN-NC", 2026, "SEN", "North Carolina", "NC",
         incumbent_party="R", incumbent_name="Thom Tillis",
         open_seat=True, open_reason="retiring",
         primary_date="2026-03-03"),

    # Ohio — SPECIAL ELECTION (Vance resigned Jan 2025 to become VP)
    Race("2026-SEN-OH-S", 2026, "SEN", "Ohio", "OH",
         incumbent_party="R", incumbent_name=None,
         open_seat=True, open_reason="vance-resigned-vp",
         special=True, primary_date="2026-05-05",
         general_date="2026-11-03"),

    # Oklahoma — incumbent running (Markwayne Mullin, Class II)
    Race("2026-SEN-OK", 2026, "SEN", "Oklahoma", "OK",
         incumbent_party="R", incumbent_name="Markwayne Mullin",
         primary_date="2026-06-16"),

    # Oregon — incumbent running
    Race("2026-SEN-OR", 2026, "SEN", "Oregon", "OR",
         incumbent_party="D", incumbent_name="Jeff Merkley",
         primary_date="2026-05-19"),

    # Rhode Island — incumbent running
    Race("2026-SEN-RI", 2026, "SEN", "Rhode Island", "RI",
         incumbent_party="D", incumbent_name="Jack Reed",
         primary_date="2026-09-08"),

    # South Carolina — incumbent running
    Race("2026-SEN-SC", 2026, "SEN", "South Carolina", "SC",
         incumbent_party="R", incumbent_name="Lindsey Graham",
         primary_date="2026-06-09"),

    # South Dakota — incumbent running (Mike Rounds, Class II)
    Race("2026-SEN-SD", 2026, "SEN", "South Dakota", "SD",
         incumbent_party="R", incumbent_name="Mike Rounds",
         primary_date="2026-06-02"),

    # Tennessee — incumbent running (Bill Hagerty, Class II)
    Race("2026-SEN-TN", 2026, "SEN", "Tennessee", "TN",
         incumbent_party="R", incumbent_name="Bill Hagerty",
         primary_date="2026-08-06"),

    # Texas — incumbent running
    Race("2026-SEN-TX", 2026, "SEN", "Texas", "TX",
         incumbent_party="R", incumbent_name="John Cornyn",
         primary_date="2026-03-03"),

    # Virginia — incumbent running
    Race("2026-SEN-VA", 2026, "SEN", "Virginia", "VA",
         incumbent_party="D", incumbent_name="Mark Warner",
         primary_date="2026-08-04"),

    # Washington — open seat (Murray retiring)
    Race("2026-SEN-WA", 2026, "SEN", "Washington", "WA",
         incumbent_party="D", incumbent_name="Patty Murray",
         open_seat=True, open_reason="retiring",
         primary_date="2026-08-04"),

    # West Virginia — incumbent running (Shelley Moore Capito, Class II)
    Race("2026-SEN-WV", 2026, "SEN", "West Virginia", "WV",
         incumbent_party="R", incumbent_name="Shelley Moore Capito",
         primary_date="2026-05-12"),

    # Wyoming — open seat (Lummis retiring)
    Race("2026-SEN-WY", 2026, "SEN", "Wyoming", "WY",
         incumbent_party="R", incumbent_name="Cynthia Lummis",
         open_seat=True, open_reason="retiring",
         primary_date="2026-08-18"),
]


# ---------------------------------------------------------------------------
# 2026 Gubernatorial races — 36 states
#
# NOT holding governor races in 2026 (odd-year or no race):
#   Louisiana (odd-year, 2027), Mississippi (odd-year, 2027),
#   New Jersey (odd-year, 2025), Virginia (odd-year, 2025),
#   Indiana, North Carolina, North Dakota, Utah, Washington, West Virginia
#   (governors elected in non-2026 years)
#
# Term-limited incumbents (cannot run again):
#   AL: Kay Ivey, AK: Mike Dunleavy, CA: Gavin Newsom, CO: Jared Polis,
#   FL: Ron DeSantis, GA: Brian Kemp, KS: Laura Kelly, KY: Andy Beshear,
#   ME: Janet Mills, MI: Gretchen Whitmer, NM: Michelle Lujan Grisham,
#   OH: Mike DeWine, OK: Kevin Stitt, SC: Henry McMaster, TN: Bill Lee,
#   WY: Mark Gordon
#
# Retiring (eligible but not running):
#   IA: Kim Reynolds, MN: Tim Walz, WI: Tony Evers
# ---------------------------------------------------------------------------
GOVERNOR_RACES_2026 = [
    # Alabama — open (Kay Ivey term-limited)
    Race("2026-GOV-AL", 2026, "GOV", "Alabama", "AL",
         incumbent_party="R", incumbent_name="Kay Ivey",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-05-19"),

    # Alaska — open (Mike Dunleavy term-limited)
    Race("2026-GOV-AK", 2026, "GOV", "Alaska", "AK",
         incumbent_party="R", incumbent_name="Mike Dunleavy",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-08-18"),

    # Arizona — incumbent running
    Race("2026-GOV-AZ", 2026, "GOV", "Arizona", "AZ",
         incumbent_party="D", incumbent_name="Katie Hobbs",
         primary_date="2026-07-21"),

    # Arkansas — incumbent running
    Race("2026-GOV-AR", 2026, "GOV", "Arkansas", "AR",
         incumbent_party="R", incumbent_name="Sarah Huckabee Sanders",
         primary_date="2026-03-03"),

    # California — open (Newsom term-limited)
    Race("2026-GOV-CA", 2026, "GOV", "California", "CA",
         incumbent_party="D", incumbent_name="Gavin Newsom",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-06-02"),

    # Colorado — open (Polis term-limited)
    Race("2026-GOV-CO", 2026, "GOV", "Colorado", "CO",
         incumbent_party="D", incumbent_name="Jared Polis",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-06-30"),

    # Connecticut — incumbent running
    Race("2026-GOV-CT", 2026, "GOV", "Connecticut", "CT",
         incumbent_party="D", incumbent_name="Ned Lamont",
         primary_date="2026-08-11"),

    # Florida — open (DeSantis term-limited)
    Race("2026-GOV-FL", 2026, "GOV", "Florida", "FL",
         incumbent_party="R", incumbent_name="Ron DeSantis",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-08-18"),

    # Georgia — open (Kemp term-limited)
    Race("2026-GOV-GA", 2026, "GOV", "Georgia", "GA",
         incumbent_party="R", incumbent_name="Brian Kemp",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-05-19"),

    # Hawaii — incumbent running (Josh Green)
    Race("2026-GOV-HI", 2026, "GOV", "Hawaii", "HI",
         incumbent_party="D", incumbent_name="Josh Green",
         primary_date="2026-08-08"),

    # Idaho — incumbent running (Brad Little)
    Race("2026-GOV-ID", 2026, "GOV", "Idaho", "ID",
         incumbent_party="R", incumbent_name="Brad Little",
         primary_date="2026-05-19"),

    # Illinois — incumbent running (JB Pritzker)
    Race("2026-GOV-IL", 2026, "GOV", "Illinois", "IL",
         incumbent_party="D", incumbent_name="JB Pritzker",
         primary_date="2026-03-17"),

    # Iowa — open (Kim Reynolds retiring)
    Race("2026-GOV-IA", 2026, "GOV", "Iowa", "IA",
         incumbent_party="R", incumbent_name="Kim Reynolds",
         open_seat=True, open_reason="retiring",
         primary_date="2026-06-02"),

    # Kansas — open (Laura Kelly term-limited)
    Race("2026-GOV-KS", 2026, "GOV", "Kansas", "KS",
         incumbent_party="D", incumbent_name="Laura Kelly",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-08-04"),

    # Kentucky — open (Andy Beshear term-limited)
    Race("2026-GOV-KY", 2026, "GOV", "Kentucky", "KY",
         incumbent_party="D", incumbent_name="Andy Beshear",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-05-19"),

    # Maine — open (Janet Mills term-limited)
    Race("2026-GOV-ME", 2026, "GOV", "Maine", "ME",
         incumbent_party="D", incumbent_name="Janet Mills",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-06-09"),

    # Maryland — incumbent running (Wes Moore)
    Race("2026-GOV-MD", 2026, "GOV", "Maryland", "MD",
         incumbent_party="D", incumbent_name="Wes Moore",
         primary_date="2026-06-23"),

    # Massachusetts — incumbent running (Maura Healey)
    Race("2026-GOV-MA", 2026, "GOV", "Massachusetts", "MA",
         incumbent_party="D", incumbent_name="Maura Healey",
         primary_date="2026-09-03"),

    # Michigan — open (Gretchen Whitmer term-limited)
    Race("2026-GOV-MI", 2026, "GOV", "Michigan", "MI",
         incumbent_party="D", incumbent_name="Gretchen Whitmer",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-08-04"),

    # Minnesota — open (Tim Walz retiring, ran for VP)
    Race("2026-GOV-MN", 2026, "GOV", "Minnesota", "MN",
         incumbent_party="D", incumbent_name="Tim Walz",
         open_seat=True, open_reason="retiring",
         primary_date="2026-08-11"),

    # Missouri — incumbent running (Mike Kehoe)
    Race("2026-GOV-MO", 2026, "GOV", "Missouri", "MO",
         incumbent_party="R", incumbent_name="Mike Kehoe",
         primary_date="2026-08-04"),

    # Nebraska — incumbent running (Jim Pillen)
    Race("2026-GOV-NE", 2026, "GOV", "Nebraska", "NE",
         incumbent_party="R", incumbent_name="Jim Pillen",
         primary_date="2026-05-12"),

    # Nevada — incumbent running (Joe Lombardo)
    Race("2026-GOV-NV", 2026, "GOV", "Nevada", "NV",
         incumbent_party="R", incumbent_name="Joe Lombardo",
         primary_date="2026-06-09"),

    # New Hampshire — incumbent running (Kelly Ayotte)
    Race("2026-GOV-NH", 2026, "GOV", "New Hampshire", "NH",
         incumbent_party="R", incumbent_name="Kelly Ayotte",
         primary_date="2026-09-08"),

    # New Jersey — open (Phil Murphy term-limited; general Nov 2026 but primary June 2026)
    Race("2026-GOV-NJ", 2026, "GOV", "New Jersey", "NJ",
         incumbent_party="D", incumbent_name="Phil Murphy",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-06-02"),

    # New Mexico — open (Michelle Lujan Grisham term-limited)
    Race("2026-GOV-NM", 2026, "GOV", "New Mexico", "NM",
         incumbent_party="D", incumbent_name="Michelle Lujan Grisham",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-06-02"),

    # New York — incumbent running (Kathy Hochul)
    Race("2026-GOV-NY", 2026, "GOV", "New York", "NY",
         incumbent_party="D", incumbent_name="Kathy Hochul",
         primary_date="2026-06-23"),

    # Ohio — open (Mike DeWine term-limited)
    Race("2026-GOV-OH", 2026, "GOV", "Ohio", "OH",
         incumbent_party="R", incumbent_name="Mike DeWine",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-08-04"),

    # Oklahoma — open (Kevin Stitt term-limited)
    Race("2026-GOV-OK", 2026, "GOV", "Oklahoma", "OK",
         incumbent_party="R", incumbent_name="Kevin Stitt",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-06-24"),

    # Oregon — incumbent running (Tina Kotek)
    Race("2026-GOV-OR", 2026, "GOV", "Oregon", "OR",
         incumbent_party="D", incumbent_name="Tina Kotek",
         primary_date="2026-05-19"),

    # Pennsylvania — incumbent running (Josh Shapiro)
    Race("2026-GOV-PA", 2026, "GOV", "Pennsylvania", "PA",
         incumbent_party="D", incumbent_name="Josh Shapiro",
         primary_date="2026-05-19"),

    # Rhode Island — incumbent running (Dan McKee)
    Race("2026-GOV-RI", 2026, "GOV", "Rhode Island", "RI",
         incumbent_party="D", incumbent_name="Dan McKee",
         primary_date="2026-09-08"),

    # South Carolina — open (Henry McMaster term-limited)
    Race("2026-GOV-SC", 2026, "GOV", "South Carolina", "SC",
         incumbent_party="R", incumbent_name="Henry McMaster",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-06-09"),

    # South Dakota — incumbent running (Larry Rhoden)
    Race("2026-GOV-SD", 2026, "GOV", "South Dakota", "SD",
         incumbent_party="R", incumbent_name="Larry Rhoden",
         primary_date="2026-06-02"),

    # Tennessee — open (Bill Lee term-limited)
    Race("2026-GOV-TN", 2026, "GOV", "Tennessee", "TN",
         incumbent_party="R", incumbent_name="Bill Lee",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-08-06"),

    # Texas — incumbent running (Greg Abbott)
    Race("2026-GOV-TX", 2026, "GOV", "Texas", "TX",
         incumbent_party="R", incumbent_name="Greg Abbott",
         primary_date="2026-03-03"),

    # Vermont — incumbent status unclear (Phil Scott; intent not confirmed)
    Race("2026-GOV-VT", 2026, "GOV", "Vermont", "VT",
         incumbent_party="R", incumbent_name="Phil Scott",
         primary_date="2026-08-11"),

    # Wisconsin — open (Tony Evers retiring after 2 terms)
    Race("2026-GOV-WI", 2026, "GOV", "Wisconsin", "WI",
         incumbent_party="D", incumbent_name="Tony Evers",
         open_seat=True, open_reason="retiring",
         primary_date="2026-08-11"),

    # Wyoming — open (Mark Gordon term-limited)
    Race("2026-GOV-WY", 2026, "GOV", "Wyoming", "WY",
         incumbent_party="R", incumbent_name="Mark Gordon",
         open_seat=True, open_reason="term-limited",
         primary_date="2026-08-18"),
]


# ---------------------------------------------------------------------------
# House races — all 435 districts
#
# Incumbent data is populated by scrapers/ballotpedia.py.
# Run that scraper to refresh. Until then, stubs are generated here.
#
# Known open seats as of 2026-04-07 (from research):
#   Retiring: Nancy Pelosi (CA-11), Jerry Nadler (NY-12), Jan Schakowsky (IL-9),
#             Danny Davis (IL-7), Chuy García (IL-4), Dwight Evans (PA-3),
#             Bonnie Watson Coleman (NJ-12), Jared Golden (ME-2), Darrell Issa (CA-41),
#             Vern Buchanan (FL), Barry Loudermilk (GA), Michael McCaul (TX-10),
#             Morgan Luttrell (TX-8), and others
#   Running for Senate: Jasmine Crockett (TX-30), Raja Krishnamoorthi (IL-8),
#             Robin Kelly (IL-2), Angie Craig (MN-3), Haley Stevens (MI-11),
#             Chris Pappas (NH-1), Barry Moore (AL-1), Andy Barr (KY-6),
#             Buddy Carter (GA-1), Mike Collins (GA-10), Wesley Hunt (TX-38)
#   Running for Governor: Andy Biggs (AZ-5), Tom Tiffany (WI), John James (MI-10),
#             John Rose (TN-2), Dusty Johnson (SD-1), Randy Feenstra (IA),
#             Eric Swalwell (CA-14), Sam Graves (MO-6)
#   Vacancies: MTG (GA-14) resigned Jan 2026, Doug LaMalfa (CA-1) died Jan 2026,
#              Mikie Sherrill (NJ-11) resigned Nov 2025
#
# Seat counts per state (2020 census apportionment):
# ---------------------------------------------------------------------------
HOUSE_SEATS = {
    "AL": 7,  "AK": 1,  "AZ": 9,  "AR": 4,  "CA": 52, "CO": 8,  "CT": 5,
    "DE": 1,  "FL": 28, "GA": 14, "HI": 2,  "ID": 2,  "IL": 17, "IN": 9,
    "IA": 4,  "KS": 4,  "KY": 6,  "LA": 6,  "ME": 2,  "MD": 8,  "MA": 9,
    "MI": 13, "MN": 8,  "MS": 4,  "MO": 8,  "MT": 2,  "NE": 3,  "NV": 4,
    "NH": 2,  "NJ": 12, "NM": 3,  "NY": 26, "NC": 14, "ND": 1,  "OH": 15,
    "OK": 5,  "OR": 6,  "PA": 17, "RI": 2,  "SC": 7,  "SD": 1,  "TN": 9,
    "TX": 38, "UT": 4,  "VT": 1,  "VA": 11, "WA": 10, "WV": 2,  "WI": 8,
    "WY": 1,
}

# Known open seats / non-incumbents as of 2026-04-07
# Format: "race_id": {"open_reason": "...", "incumbent_name": "...", "incumbent_party": "..."}
HOUSE_KNOWN_OPEN = {
    "2026-H-AL-01": {"open_reason": "running-for-senate", "incumbent_name": "Barry Moore", "incumbent_party": "R"},
    "2026-H-AZ-05": {"open_reason": "running-for-governor", "incumbent_name": "Andy Biggs", "incumbent_party": "R"},
    "2026-H-CA-01": {"open_reason": "died", "incumbent_name": "Doug LaMalfa", "incumbent_party": "R"},
    "2026-H-CA-11": {"open_reason": "retiring", "incumbent_name": "Nancy Pelosi", "incumbent_party": "D"},
    "2026-H-CA-14": {"open_reason": "running-for-governor", "incumbent_name": "Eric Swalwell", "incumbent_party": "D"},
    "2026-H-CA-41": {"open_reason": "retiring", "incumbent_name": "Darrell Issa", "incumbent_party": "R"},
    "2026-H-GA-01": {"open_reason": "running-for-senate", "incumbent_name": "Buddy Carter", "incumbent_party": "R"},
    "2026-H-GA-10": {"open_reason": "running-for-senate", "incumbent_name": "Mike Collins", "incumbent_party": "R"},
    "2026-H-GA-14": {"open_reason": "resigned", "incumbent_name": "Marjorie Taylor Greene", "incumbent_party": "R"},
    "2026-H-IA-04": {"open_reason": "running-for-governor", "incumbent_name": "Randy Feenstra", "incumbent_party": "R"},
    "2026-H-IL-02": {"open_reason": "running-for-senate", "incumbent_name": "Robin Kelly", "incumbent_party": "D"},
    "2026-H-IL-04": {"open_reason": "retiring", "incumbent_name": "Chuy García", "incumbent_party": "D"},
    "2026-H-IL-07": {"open_reason": "retiring", "incumbent_name": "Danny Davis", "incumbent_party": "D"},
    "2026-H-IL-08": {"open_reason": "running-for-senate", "incumbent_name": "Raja Krishnamoorthi", "incumbent_party": "D"},
    "2026-H-IL-09": {"open_reason": "retiring", "incumbent_name": "Jan Schakowsky", "incumbent_party": "D"},
    "2026-H-KY-06": {"open_reason": "running-for-senate", "incumbent_name": "Andy Barr", "incumbent_party": "R"},
    "2026-H-ME-02": {"open_reason": "retiring", "incumbent_name": "Jared Golden", "incumbent_party": "D"},
    "2026-H-MI-10": {"open_reason": "running-for-governor", "incumbent_name": "John James", "incumbent_party": "R"},
    "2026-H-MI-11": {"open_reason": "running-for-senate", "incumbent_name": "Haley Stevens", "incumbent_party": "D"},
    "2026-H-MN-02": {"open_reason": "running-for-senate", "incumbent_name": "Angie Craig", "incumbent_party": "D"},
    "2026-H-MO-06": {"open_reason": "running-for-governor", "incumbent_name": "Sam Graves", "incumbent_party": "R"},
    "2026-H-NH-01": {"open_reason": "running-for-senate", "incumbent_name": "Chris Pappas", "incumbent_party": "D"},
    "2026-H-NJ-11": {"open_reason": "resigned", "incumbent_name": "Mikie Sherrill", "incumbent_party": "D"},
    "2026-H-NJ-12": {"open_reason": "retiring", "incumbent_name": "Bonnie Watson Coleman", "incumbent_party": "D"},
    "2026-H-NY-07": {"open_reason": "retiring", "incumbent_name": "Nydia Velázquez", "incumbent_party": "D"},
    "2026-H-NY-12": {"open_reason": "retiring", "incumbent_name": "Jerry Nadler", "incumbent_party": "D"},
    "2026-H-PA-03": {"open_reason": "retiring", "incumbent_name": "Dwight Evans", "incumbent_party": "D"},
    "2026-H-SD-01": {"open_reason": "running-for-governor", "incumbent_name": "Dusty Johnson", "incumbent_party": "R"},
    "2026-H-TN-02": {"open_reason": "running-for-governor", "incumbent_name": "John Rose", "incumbent_party": "R"},
    "2026-H-TX-08": {"open_reason": "retiring", "incumbent_name": "Morgan Luttrell", "incumbent_party": "R"},
    "2026-H-TX-10": {"open_reason": "retiring", "incumbent_name": "Michael McCaul", "incumbent_party": "R"},
    "2026-H-TX-30": {"open_reason": "running-for-senate", "incumbent_name": "Jasmine Crockett", "incumbent_party": "D"},
    "2026-H-TX-38": {"open_reason": "running-for-senate", "incumbent_name": "Wesley Hunt", "incumbent_party": "R"},
    "2026-H-WI-07": {"open_reason": "running-for-governor", "incumbent_name": "Tom Tiffany", "incumbent_party": "R"},
}


def _load_house_incumbents_json(year: int = 2026) -> dict[str, dict]:
    """
    Load House incumbent data from data/processed/house_incumbents.json if it exists.
    Run scrapers/house_incumbents.py to generate/refresh this file.
    Returns dict keyed by race_id.
    """
    import json as _json
    json_path = Path(__file__).parent.parent / "data" / "processed" / "house_incumbents.json"  # noqa
    if not json_path.exists():
        return {}
    try:
        with open(json_path, encoding="utf-8") as f:
            data = _json.load(f)
        return {m["race_id"]: m for m in data.get("members", [])}
    except Exception:
        return {}


def generate_house_races(year: int = 2026) -> list[Race]:
    """
    Generate Race objects for all 435 House districts.

    Priority for incumbent data:
      1. data/processed/house_incumbents.json (from scrapers/house_incumbents.py)
      2. HOUSE_KNOWN_OPEN fallback (manually curated open seats)
    Run `py -3 scrapers/house_incumbents.py` to populate full incumbent data.
    """
    state_name_by_abbrev = {v: k for k, v in STATE_ABBREVS.items()}
    scraped = _load_house_incumbents_json(year)

    races = []
    for abbrev, n_seats in HOUSE_SEATS.items():
        state_name = state_name_by_abbrev.get(abbrev, abbrev)
        for district in range(1, n_seats + 1):
            district_str = f"{district:02d}"
            race_id = f"{year}-H-{abbrev}-{district_str}"

            # Prefer scraped data
            if race_id in scraped:
                s = scraped[race_id]
                races.append(Race(
                    race_id=race_id,
                    year=year,
                    office="H",
                    state=state_name,
                    state_abbrev=abbrev,
                    district=district_str,
                    incumbent_party=s.get("incumbent_party"),
                    incumbent_name=s.get("incumbent_name"),
                    open_seat=bool(s.get("open_seat")),
                    open_reason=s.get("open_reason") or None,
                ))
            else:
                # Fallback to manually curated open seat list
                known = HOUSE_KNOWN_OPEN.get(race_id, {})
                races.append(Race(
                    race_id=race_id,
                    year=year,
                    office="H",
                    state=state_name,
                    state_abbrev=abbrev,
                    district=district_str,
                    incumbent_party=known.get("incumbent_party"),
                    incumbent_name=known.get("incumbent_name"),
                    open_seat=bool(known),
                    open_reason=known.get("open_reason"),
                ))
    return races


HOUSE_RACES_2026 = generate_house_races(2026)

# ---------------------------------------------------------------------------
# Combined lookup
# ---------------------------------------------------------------------------
ALL_RACES_2026: list[Race] = SENATE_RACES_2026 + GOVERNOR_RACES_2026 + HOUSE_RACES_2026
RACE_BY_ID: dict[str, Race] = {r.race_id: r for r in ALL_RACES_2026}


def get_race(race_id: str) -> Race:
    if race_id not in RACE_BY_ID:
        raise KeyError(f"Unknown race_id: {race_id}")
    return RACE_BY_ID[race_id]


if __name__ == "__main__":
    special = [r for r in ALL_RACES_2026 if r.special]
    open_seats = [r for r in ALL_RACES_2026 if r.open_seat]
    senate_open = [r for r in SENATE_RACES_2026 if r.open_seat]
    gov_open = [r for r in GOVERNOR_RACES_2026 if r.open_seat]
    house_open = [r for r in HOUSE_RACES_2026 if r.open_seat]

    print(f"Senate races:   {len(SENATE_RACES_2026):>4}  (open: {len(senate_open)}, special: {len(special)})")
    print(f"Governor races: {len(GOVERNOR_RACES_2026):>4}  (open: {len(gov_open)})")
    print(f"House races:    {len(HOUSE_RACES_2026):>4}  (known open: {len(house_open)})")
    print(f"Total:          {len(ALL_RACES_2026):>4}")
    print()
    print("Senate open seats:")
    for r in senate_open:
        print(f"  {r.race_id:20} {r.incumbent_name} ({r.open_reason})")
    print()
    print("Senate special elections:")
    for r in special:
        print(f"  {r.race_id:20} {r.open_reason}")
    print()
    print("Governor open seats:")
    for r in gov_open:
        print(f"  {r.race_id:20} {r.incumbent_name} ({r.open_reason})")
