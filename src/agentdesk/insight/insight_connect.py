"""The connect step (spec 023, ticket 023-1): what a card shows, read
against what the desk already holds.

Plugins bring rows; this module says which of the names in a row the
desk already knows, and what it holds about them, so a card can carry
one honest line: "Also in your desk: Halvorsen Freight, 3 things you
told the desk, renewal call today at 14:38." The match is deterministic
and model-free, so it works with no key and cannot invent a
relationship: a name is either here or it is not.

Nothing user-facing names the mechanism. The index is built once per
dashboard read (`insight/context.py`), a sealed or missing memory
contributes no names, and a family that finds no match says nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

# The entity types a line may name: concepts are excluded, the same
# standing rule the meeting rule keeps.
NAME_TYPES = ("org", "person")
# A name whose longest word is under this many letters draws nothing:
# short names match too much, and a card must never claim a link the
# desk cannot defend.
MIN_LONGEST_WORD = 6
# At most three names on one card, in the order the rows name them.
MAX_PER_CARD = 3


@dataclass(frozen=True)
class DeskName:
    """One thing the desk holds: the entity's id, every name it answers
    to, and how many facts stand behind it."""

    entity_id: int
    names: tuple[str, ...]
    fact_count: int
    # The next event's clause ("renewal call today at 14:38") when the
    # agenda names this entity, else "".
    event_clause: str = ""


def build_index(entities, agenda: dict | None, now: float) -> tuple[DeskName, ...]:
    """The desk's nameable things, from the memory store's entity list.

    `entities` is whatever `memory.entities()` returned (already [] for a
    missing store). Only org and person entities enter, a single-word
    alias is refused, and the agenda's title names at most one of them
    with its event clause. `now` dates the clause; a time it cannot read
    leaves the clause at the bare title, never at nothing."""
    names: list[DeskName] = []
    for entity in entities or ():
        kind = str(getattr(entity, "type", "") or "")
        if kind not in NAME_TYPES:
            continue
        canonical = str(getattr(entity, "canonical_name", "") or "").strip()
        if not canonical:
            continue
        aliases = tuple(
            str(alias).strip() for alias in (getattr(entity, "aliases", ()) or ())
            if str(alias).strip() and len(str(alias).split()) > 1
        )
        names.append(DeskName(
            entity_id=int(getattr(entity, "id", 0)),
            names=(canonical, *aliases),
            fact_count=int(getattr(entity, "fact_count", 0) or 0),
        ))
    if agenda and str(agenda.get("title") or "").strip():
        named = match_names(str(agenda.get("title")), tuple(names))
        if named:
            clause = _event_clause(agenda, now)
            names = [DeskName(n.entity_id, n.names, n.fact_count, clause)
                     if n.entity_id == named[0].entity_id else n for n in names]
    return tuple(names)


def match_names(text: str, index: tuple[DeskName, ...]) -> list[DeskName]:
    """The desk names one block of text carries, best first.

    Longest name present as a whole word wins in the text; one entry per
    entity id (a name and its alias count once); the refusals are the
    spec's. Returns at most MAX_PER_CARD, in first-named order."""
    lowered = (text or "").lower()
    if not lowered.strip() or not index:
        return []
    found: dict[int, tuple[int, int, DeskName]] = {}
    for order, entry in enumerate(index):
        best = 0
        for name in entry.names:
            if _longest_word(name) < MIN_LONGEST_WORD:
                continue
            hit = re.search(rf"\b{re.escape(name.lower())}\b", lowered)
            if hit and len(name) > best:
                best = len(name)
        if best:
            found[entry.entity_id] = (best, order, entry)
    ranked = sorted(found.values(), key=lambda triple: (-triple[0], triple[1]))
    return [entry for _best, _order, entry in ranked[:MAX_PER_CARD]]


def connect_line(rows: list[str], index: tuple[DeskName, ...]) -> tuple[str, int]:
    """The card's line, and how many remembered notes it drew on.

    Rows are the card's label texts, in the order the card shows them.
    No match draws no line (""), so a card with nothing to say stays
    quiet; a match names each entity once, in the order the rows name
    them (a row's own matches in the order they appear in the text),
    with its fact count and its event clause when there is one."""
    picked: dict[int, tuple[int, DeskName]] = {}
    for position, row in enumerate(rows or ()):
        lowered = str(row).lower()
        if not lowered.strip():
            continue
        hits = []
        for entry in index:
            best_at, best_name = -1, ""
            for name in entry.names:
                if _longest_word(name) < MIN_LONGEST_WORD:
                    continue
                found = re.search(rf"\b{re.escape(name.lower())}\b", lowered)
                if not found:
                    continue
                if best_at == -1 or found.start() < best_at:
                    best_at, best_name = found.start(), name
            if best_at >= 0:
                hits.append((best_at, entry.entity_id, entry))
        for _at, _id, entry in sorted(hits, key=lambda hit: hit[0]):
            picked.setdefault(entry.entity_id, (position, entry))
    if not picked:
        return "", 0
    ordered = [entry for _position, entry in
               sorted(picked.values(), key=lambda pair: pair[0])[:MAX_PER_CARD]]
    clauses = []
    total = 0
    for entry in ordered:
        total += entry.fact_count
        noun = "thing" if entry.fact_count == 1 else "things"
        clause = f"{entry.names[0]}, {entry.fact_count} {noun} you told the desk"
        if entry.event_clause:
            clause += f", {entry.event_clause}"
        clauses.append(clause)
    return "Also in your desk: " + "; ".join(clauses) + ".", total


def _event_clause(agenda: dict, now: float) -> str:
    """The agenda's next meeting as one clause: title, day, clock time."""
    title = str(agenda.get("title") or "").strip().lower()
    when = str(agenda.get("time") or "").strip()
    day = ""
    clock = ""
    if when:
        try:
            start = datetime.fromisoformat(when)
        except ValueError:
            start = None
        if start is not None:
            clock = start.strftime("%H:%M")
            today = datetime.fromtimestamp(now, tz=timezone.utc).astimezone().date()
            delta = (start.date() - today).days
            day = {0: "today", 1: "tomorrow"}.get(delta, start.strftime("%b %d"))
    return " ".join(part for part in (title, day, f"at {clock}" if clock else "") if part)


def _longest_word(name: str) -> int:
    """The longest word a name holds, in characters."""
    words = [word for word in re.split(r"\W+", name) if word]
    return max((len(word) for word in words), default=0)
