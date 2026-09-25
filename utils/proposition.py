"""Outcome signature: what a market title actually resolves on.

Title similarity says two markets are ABOUT the same thing; it can't say
they resolve on the same OUTCOME. Every fake pair found in the
2026-09-23 review differed in exactly one of these fields while scoring
80+ on token_sort_ratio:

  position  win / place-2 / place-3 / top-N / last / qualify
            ("Big Brother — 2nd place — X" vs "Will X win Big Brother?")
  period    full game / 1st half / 2nd half
            ("BTTS" vs "Both Teams to Score in Second Half")
  division  women / men / open
            ("Chess Olympiad Women's — China" vs "... Open Tournament")
  teams     the two sides of an "A vs B" fixture
            ("Austria vs Israel" vs "Australia vs Brazil")
  game_date fixture date (series repeat the same matchup on consecutive days)
  day_scope a specific calendar day vs a whole month/year
            ("How high will XRP get in September" vs "above $1.70 on Sept 25")
  person    every name token of the subject
            ("Mojtaba Khamenei" vs "Will Ali Khamenei be ...")

`incompatibility(a, b)` returns the first field on which two titles
disagree (or None). It's a gate, not a matcher: fuzzy scoring still
proposes candidates; this rejects any candidate whose outcome differs.
"""

import re
import unicodedata

from rapidfuzz import fuzz

_ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
        "fourth": 4, "4th": 4, "fifth": 5, "5th": 5}


def fold(s) -> str:
    """Lowercase, strip diacritics, hyphens/dashes -> space."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"\s+", " ", re.sub(r"[-‐–—_/]", " ", s)).strip()


def position(title) -> str | None:
    """Placement the market pays on; None when unspecified (treated as
    'win-compatible': "2028 Democratic nominee — X" ≈ "Will X win the
    nomination?")."""
    t = fold(title)
    if re.search(r"\bqualif(?:y|ies|ier|iers|ication)\b", t):
        return "qualify"
    if re.search(r"\b(?:finish(?:es)?|place[sd]?|come in)\s+last\b|\blast place\b", t):
        return "last"
    # "Dancing with the Stars — Finalists — X" vs "Will X win DWTS?" scored
    # 134% fake returns (2026-09-24): reaching the final ≠ winning it.
    if re.search(r"\bsemi ?finalists?\b|\bsemi ?finals?\b", t):
        return "semifinal"
    if re.search(r"\bfinalists?\b|\b(?:reach|make|advance to)\b.{0,40}?\bfinals?\b", t):
        return "finalist"
    m = re.search(r"\btop\s+(\d+)\b", t)
    if m:
        return f"top{m.group(1)}"
    m = re.search(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)[\s-]*place\b", t)
    if m:
        n = _ORD[m.group(1)]
        return "win" if n == 1 else f"place{n}"
    if re.search(r"\b(?:winner|champion|top finisher|win|wins|won)\b", t):
        return "win"
    return None


def period(title) -> str | None:
    t = fold(title)
    if re.search(r"\b(?:second|2nd)\s+half\b|\b2h\b", t):
        return "2H"
    if re.search(r"\b(?:first|1st)\s+half\b|\b1h\b", t):
        return "1H"
    return None


def division(title) -> str | None:
    t = fold(title)
    if re.search(r"\bwomen'?s?\b|\bwomens\b|\bladies\b", t):
        return "women"
    if re.search(r"\bmen'?s?\b", t):
        return "men"
    if re.search(r"\bopen tournament\b|\bopen section\b", t):
        return "open"
    return None


def _bound(title) -> str | None:
    t = fold(title)
    if re.search(r"\bor (?:above|higher|more|greater|stronger)\b|\bat least\b", t):
        return "gte"
    if re.search(r"\bpeak (?:at|as)\b|\bexactly\b", t):
        return "eq"
    return None


def _category(title) -> str | None:
    m = re.search(r"\bcategory\s*(\d)\b", fold(title))
    return m.group(1) if m else None


_TEAM_STOP = {"fc", "sc", "cf", "cd", "ec", "afc", "club", "the", "of", "and",
              "de", "la", "del", "republic", "city", "united", "real", "sporting",
              "athletic", "atletico", "st", "saint", "ir", "e", "y"}


def _team_tokens(name) -> set:
    toks = re.findall(r"[a-z0-9]+", fold(name).replace("&", " "))
    return {t for t in toks if len(t) >= 3 and t not in _TEAM_STOP}


def teams(title):
    """(teamA, teamB) token sets for "A vs B" titles, else None."""
    t = str(title or "")
    # "... ?: Toronto Blue Jays vs. Baltimore Orioles" -> take the part
    # after a question-colon; Kalshi "A vs B: BTTS — ..." -> before colon.
    if "?:" in t:
        t = t.split("?:", 1)[1]
    m = re.search(r"([^:?—–]+?)\s+vs\.?\s+([^:?—–]+)", t, re.IGNORECASE)
    if not m:
        return None
    a, b = _team_tokens(m.group(1)), _team_tokens(m.group(2))
    return (a, b) if a and b else None


def _teams_compatible(ta, tb) -> bool:
    (a1, a2), (b1, b2) = ta, tb
    return bool((a1 & b1 and a2 & b2) or (a1 & b2 and a2 & b1))


def score(title):
    """Exact-score markets -> list of (team_tokens, goals), else None.

    Kalshi:     "Poland vs Bosnia and Herzegovina: Correct Score — Poland wins 2-0"
    Polymarket: "Exact Score: Poland 0 - 2 Bosnia and Herzegovina?"
    The Polymarket one is a BOSNIA 2-0 win: order in the title is the
    fixture order, not winner-first (fake pair 2026-09-24)."""
    t = str(title or "")
    m = re.search(r"exact score:\s*(.+?)\s+(\d+)\s*-\s*(\d+)\s+(.+?)\s*\??\s*$", t, re.IGNORECASE)
    if m:
        return [(_team_tokens(m.group(1)), int(m.group(2))),
                (_team_tokens(m.group(4)), int(m.group(3)))]
    m = re.search(r"^(.+?)\s+vs\.?\s+(.+?):\s*correct score\s*[—–-]\s*(.+?)\s+wins\s+(\d+)\s*-\s*(\d+)",
                  t, re.IGNORECASE)
    if m:
        home, away, winner = (_team_tokens(m.group(i)) for i in (1, 2, 3))
        hi, lo = sorted((int(m.group(4)), int(m.group(5))), reverse=True)
        return [(home, hi if home & winner else lo), (away, hi if away & winner else lo)]
    m = re.search(r"^(.+?)\s+vs\.?\s+(.+?):\s*correct score\s*[—–-]\s*(?:draw|tie)\s+(\d+)\s*-\s*(\d+)",
                  t, re.IGNORECASE)
    if m:
        g = int(m.group(3))
        return [(_team_tokens(m.group(1)), g), (_team_tokens(m.group(2)), g)]
    return None


def _scores_compatible(sa, sb) -> bool:
    for toks_a, goals_a in sa:
        match = [g for toks_b, g in sb if toks_a & toks_b]
        if not match or match[0] != goals_a:
            return False
    return True


_MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"


def day_scope(title) -> bool:
    """True if the title pins a specific calendar day ("on September 25",
    "Sept 25", "2026-09-25"). Year-end phrasing ("December 31, 2026",
    "Dec 31") is a year window, not a day, so it doesn't count."""
    t = fold(title)
    for m in re.finditer(rf"\b({_MONTHS})\.?\s+(\d{{1,2}})\b", t):
        if not (m.group(1).startswith("dec") and m.group(2) == "31"):
            return True
    return bool(re.search(r"\b20\d\d-\d\d-\d\d\b", t))


_NAME_STOP = {"the", "jr", "sr", "ii", "iii", "iv", "of", "and", "de", "la", "van", "von", "bin", "al"}


def _subject(title) -> str | None:
    """Kalshi-style trailing subject: text after the last em/en dash."""
    t = str(title or "")
    for sep in ("—", "–"):
        if sep in t:
            return t.rsplit(sep, 1)[1].strip()
    return None


def person_mismatch(a, b) -> bool:
    """A multi-word trailing subject on one side must appear token-by-token
    (fuzzily, for spelling variants like Vasil/Vassil) in the other title.
    Single-word subjects are left to the existing subject guards (country
    names like "USA" vs "the US" need their own aliasing)."""
    for x, y in ((a, b), (b, a)):
        subj = _subject(x)
        # Only proper names: every significant word capitalised, no digits.
        # "Game goes to extra innings" / "Category 5 or above" are outcome
        # labels, not people, and their wording legitimately varies.
        if not subj or re.search(r"\d", subj):
            continue
        words = [w for w in re.findall(r"[^\W\d_][\w'.]*", subj) if len(w) >= 3
                 and fold(w) not in _NAME_STOP]
        if not words or not all(w[0].isupper() for w in words):
            continue
        toks = [t for t in re.findall(r"[a-z]+", fold(subj)) if len(t) >= 3 and t not in _NAME_STOP]
        if len(toks) < 2:
            continue
        other = re.findall(r"[a-z]+", fold(y))
        for tok in toks:
            if not any(fuzz.ratio(tok, o) >= 85 for o in other):
                return True
    return False


def incompatibility(a, b, game_date_a=None, game_date_b=None) -> str | None:
    """First outcome field on which titles `a` and `b` disagree, else None."""
    # pandas hands missing dates over as NaN, which is truthy.
    game_date_a = game_date_a if isinstance(game_date_a, str) else None
    game_date_b = game_date_b if isinstance(game_date_b, str) else None
    pa, pb = position(a), position(b)
    if pa != pb and not ({pa, pb} <= {"win", None}):
        return f"position {pa} vs {pb}"
    if period(a) != period(b):
        return f"period {period(a)} vs {period(b)}"
    da, db = division(a), division(b)
    if da != db and da is not None and db is not None:
        return f"division {da} vs {db}"
    if (da is None) != (db is None) and "women" in (da, db):
        return f"division {da} vs {db}"
    # Storm category ("any Category 5 hurricane in the US" vs "a hurricane
    # in Hawaii") — one-sided counts as a mismatch.
    ca, cb = _category(a), _category(b)
    if ca != cb:
        return f"category {ca} vs {cb}"
    # "Category 2 or above" (≥) vs "peak at Category 2" (exactly): Kalshi
    # 99.5¢ vs Polymarket 0.5¢ once Polo passed Cat 2. Equal only at the
    # top of a scale (Cat 5).
    ba, bb = _bound(a), _bound(b)
    if ba and bb and ba != bb and ca != "5":
        return f"bound {ba} vs {bb}"
    sa, sb = score(a), score(b)
    if (sa is None) != (sb is None):
        return "score vs non-score"
    if sa and not _scores_compatible(sa, sb):
        return "different exact score"
    ta, tb = teams(a), teams(b)
    if ta and tb:
        if not _teams_compatible(ta, tb):
            return "teams differ"
        if game_date_a and game_date_b and game_date_a != game_date_b:
            return f"game date {game_date_a} vs {game_date_b}"
    if day_scope(a) != day_scope(b):
        return "day vs window"
    if person_mismatch(a, b):
        return "person differs"
    fa, fb = first_scorer(a), first_scorer(b)
    if (fa is None) != (fb is None):
        return "first-to-score vs other market"
    if fa and fb:
        if (fa[0] == "none") != (fb[0] == "none"):
            return "first scorer: neither vs a team"
        if fa[0] not in ("none", "?") and fb[0] not in ("none", "?") and fb[1]:
            if _club_sim(fa[0], fb[0]) <= _club_sim(fa[0], fb[1]):
                return "first scorer: different team"
    return None


# ── first team to score (2026-09-25) ──────────────────────────────────────
# Kalshi "Charlotte FC vs Chicago Fire: First Team to Score — Charlotte FC"
# paired with Polymarket "Chicago Fire FC to score first vs. Charlotte FC?"
# (the matcher's subject guard passed on the shared token "FC"), and with
# "...: Neither team to score first?" (the 0-0 outcome) at 50-74% "returns".

_FTS_RE = re.compile(r"first team to score|to score first|score first", re.IGNORECASE)
_NO_SCORER_RE = re.compile(r"\bneither\b|\bno goals?\b|\bno team\b", re.IGNORECASE)
# Suffix/filler words only. "city"/"united" stay: they are what tells
# Manchester City from Manchester United, NYCFC from the Red Bulls.
_CLUB_WORDS = {"fc", "sc", "cf", "afc", "cd", "ac", "club", "de", "la", "the", "fk", "sk"}


def first_scorer(title):
    """None if not a first-to-score market; ('none', None) for the no-goal
    outcome; (subject, opponent) otherwise; ('?', None) if unparsed."""
    t = str(title or "")
    if not _FTS_RE.search(t):
        return None
    if _NO_SCORER_RE.search(t):
        return ("none", None)
    m = re.match(r"^\s*(?:will\s+)?(.+?)\s+score first\s+vs\.?\s+(.+?)\s*\??\s*$", t, re.IGNORECASE) \
        or re.match(r"^\s*(?:will\s+)?(.+?)\s+to score first\s+vs\.?\s+(.+?)\s*\??\s*$", t, re.IGNORECASE)
    if m:
        subj = re.sub(r"\s+to$", "", m.group(1).strip(), flags=re.IGNORECASE)
        return (subj, m.group(2))
    m = re.match(r"^\s*(.+?)\s+vs\.?\s+(.+?):.*first team to score.*?[—–-]\s*(.+?)\s*$", t, re.IGNORECASE)
    if m:
        home, away, subj = m.group(1), m.group(2), m.group(3)
        opp = away if _club_sim(subj, home) >= _club_sim(subj, away) else home
        return (subj, opp)
    return ("?", None)


def _club_norm(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "")
    toks = [w for w in re.findall(r"[a-z0-9]+", s) if w not in _CLUB_WORDS]
    return " ".join(toks) or s


def _club_sim(x, y):
    """(token-set, plain) similarity; the plain ratio breaks token-set ties
    (derbies where one name's tokens are a subset of the other's)."""
    a, b = _club_norm(x), _club_norm(y)
    return (fuzz.token_set_ratio(a, b), fuzz.ratio(a, b))


_KALSHI_DATE = re.compile(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})")
_MON_NUM = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def kalshi_game_date(ticker) -> str | None:
    """'KXMLBEXTRAS-26SEP252215LADSF-EXTRAS' -> '2026-09-25'."""
    m = _KALSHI_DATE.search(str(ticker or "").upper())
    if not m:
        return None
    return f"20{m.group(1)}-{_MON_NUM[m.group(2)]:02d}-{m.group(3)}"


def slug_game_date(slug) -> str | None:
    """'mlb-tor-bal-2026-09-22' -> '2026-09-22'."""
    m = re.search(r"(20\d\d-\d\d-\d\d)", str(slug or ""))
    return m.group(1) if m else None
