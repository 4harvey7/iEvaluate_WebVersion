"""Name matching for instructors: catches the same person typed differently
("rodz Licayan" vs "Rodz Harvey d Licayan") without flagging different people
who only share a surname ("Rodz Licayan" vs "Ronald Kinggg Licayan").

Middle names are stored inside first_name, so the given-name check lets the
shorter side be a subset of the longer one, and lets an initial stand for a
full name ("Rodz H." = "Rodz Harvey")."""
from __future__ import annotations

import re
import unicodedata

TITLES = {"dr", "prof", "engr", "atty", "mr", "mrs", "ms", "maam", "sir", "hon"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def name_tokens(s: str | None) -> list[str]:
    """Lower-case words without accents, apostrophes, titles and suffixes."""
    text = unicodedata.normalize("NFKD", str(s or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("'", "").replace("\u2019", "")
    return [t for t in re.split(r"[^0-9a-z]+", text) if t and t not in TITLES and t not in SUFFIXES]


def surname_parts(last: str | None) -> list[str]:
    return [t for t in name_tokens(last) if len(t) > 1]


def surname_matches(a: str | None, b: str | None) -> bool:
    pa, pb = surname_parts(a), surname_parts(b)
    if not pa or not pb:
        return False
    if "".join(pa) == "".join(pb):  # Dela Cruz = Delacruz = dela-cruz
        return True
    short, long_ = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    return long_[len(long_) - len(short):] == short  # Cruz vs Dela Cruz


def given_names_match(a: str | None, b: str | None) -> bool:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    if "".join(ta) == "".join(tb):  # Mary Ann = Maryann
        return True
    full_a = [t for t in ta if len(t) > 1]
    full_b = [t for t in tb if len(t) > 1]
    if len(full_a) <= len(full_b):
        short, long_, short_full = ta, tb, full_a
    else:
        short, long_, short_full = tb, ta, full_b
    if not short_full:  # only initials ("R."): must start the long side's first name
        return short[0][0] == long_[0][0]
    remaining = list(long_)
    for t in short_full:
        if t not in remaining:
            return False
        remaining.remove(t)
    for ini in (t for t in short if len(t) == 1):
        if not remaining:
            return True
        hit = next((r for r in remaining if r[0] == ini), None)
        if hit is None:
            return False  # a conflicting initial: a different person
        remaining.remove(hit)
    return True


def is_near_duplicate(tf: str | None, tl: str | None, cf: str | None, cl: str | None) -> bool:
    return surname_matches(tl, cl) and given_names_match(tf, cf)


def same_name(tf: str | None, tl: str | None, cf: str | None, cl: str | None) -> bool:
    return "".join(name_tokens(f"{tf} {tl}")) == "".join(name_tokens(f"{cf} {cl}"))


def search_words_match(query: str | None, full_name: str | None) -> bool:
    """Every typed word is the start of some word of the name."""
    q = name_tokens(query)
    n = name_tokens(full_name)
    return bool(q) and all(any(w.startswith(t) for w in n) for t in q)
