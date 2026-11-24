"""Resolve the context a family reads, from what the dashboard already
loaded. Nothing here may raise: a missing or locked memory is a note on
the reading, never a broken dashboard.
"""

from __future__ import annotations

import time
from typing import Any

from agentdesk.insight import insight_connect
from agentdesk.insight.types import InsightContext
from agentdesk.memory.crypto import MemoryEncryptedError
from agentdesk.memory.profile import learned_lines, merged_topics

RECALL_LIMIT = 3


def build_context(
    server: Any, preferences: dict, *, now: float | None = None
) -> InsightContext:
    """Resolve everything a payload's families may read, once, up front.

    Memory and the calendar are consulted here and nowhere else, so every
    card on one payload sees the same agenda and the same recall adapter,
    and a family stays a pure function of its context. The learned
    profile (spec 016 section 6.5) is read here too: the context's topics
    become the stated ones then the learned ones, and the learned lines
    ride along under `learnedLines`. `now` defaults to the wall clock and
    is injectable so a test can fix time. The desk's nameable things
    (spec 023's connect step) ride along too: an empty tuple whenever
    memory is absent, locked or sealed, so a context built directly in a
    test behaves exactly as before."""
    fixed_now = time.time() if now is None else now
    recall, note = _memory(server)
    agenda, agenda_note = _agenda(server)
    return InsightContext(
        preferences=_with_learned(server, preferences),
        now=fixed_now,
        recall=recall,
        memory_note=note,
        agenda=agenda,
        agenda_note=agenda_note,
        desk_names=insight_connect.build_index(_entities(server), agenda, fixed_now),
    )


def _entities(server: Any) -> list:
    """The memory store's entity summaries, or [] when there are none to
    name. Every failure reads as no names: the connect step never takes
    a card down over memory."""
    memory = getattr(server, "memory", None)
    if memory is None or bool(getattr(memory, "is_sealed", False)):
        return []
    try:
        return list(memory.entities() or [])
    except Exception:  # noqa: BLE001 - a reading never takes a card down
        return []


def _with_learned(server: Any, preferences: dict) -> dict:
    """The preferences as the readings should see them, learned folded in.

    The stored preferences file is never touched: this merges over the
    copy the context carries. A desk with no learned store, or one that
    cannot be read right now, gets the stated preferences unchanged."""
    document = dict(preferences or {})
    store = getattr(server, "learned_profile", None)
    if store is None:
        return document
    try:
        learned = store.document()
    except Exception:  # noqa: BLE001 - a reading never takes a card down
        return document
    document["focusTopics"] = merged_topics(
        document.get("focusTopics"), learned.get("focusTopics"))
    lines = learned_lines(learned)
    if lines:
        document["learnedLines"] = lines
    return document


def _agenda(server: Any) -> tuple[dict | None, str]:
    """The next meeting from the calendar bridge's day view, or the reason
    there is none. One read, shared by every family on the payload."""
    settings = getattr(server, "settings", None)
    try:
        if settings is not None and not settings.get("calendar_enabled"):
            return None, "Calendar reading is off in settings."
    except KeyError:
        pass
    calendar = getattr(server, "calendar", None)
    if calendar is None:
        return None, "The calendar bridge is not available."
    try:
        view = calendar.day_view()
    except Exception:  # noqa: BLE001 - a reading never takes a card down
        return None, "The calendar could not be read just now."
    upcoming = [row for row in (view.get("upcoming") or []) if isinstance(row, dict)]
    if not upcoming:
        return None, "Nothing left on the calendar."
    first = upcoming[0]
    return {
        "title": str(first.get("title") or "Busy"),
        "time": str(first.get("time") or ""),
        "meta": str(first.get("meta") or ""),
        "prep": [
            str(item.get("text"))
            for item in (view.get("items") or [])
            if isinstance(item, dict) and item.get("text")
        ],
    }, ""


def _memory(server: Any) -> tuple[Any, str]:
    """The recall adapter, and the reason it is missing when it is."""
    memory = getattr(server, "memory", None)
    if memory is None:
        return None, "Memory is not set up on this desk, so nothing was recalled."
    if bool(getattr(memory, "is_sealed", False)):
        return None, "Memory is locked, so nothing was recalled."

    def recall(subject: str, limit: int = RECALL_LIMIT) -> list[dict]:
        """The bank's facts for a subject, as {"text", "source"} rows.

        Every failure, a lock taken mid-read included, reads as no facts:
        a reading must never take a card down over memory."""
        try:
            found = memory.recall(subject)
        except MemoryEncryptedError:
            return []
        except Exception:  # noqa: BLE001 - a reading never takes a card down
            return []
        rows = []
        for _score, fact in list(found)[:limit]:
            text = str(getattr(fact, "text", "") or "").strip()
            if text:
                rows.append({"text": text, "source": str(getattr(fact, "source", "") or "")})
        return rows

    return recall, ""
