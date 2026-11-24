"""The shapes the coupled-intelligence seam passes around.

One context in, one reading out. A family module never reaches for the
server, the preferences file or the clock itself, so a reading is a pure
function of what the dashboard already decided to show and of what the
user already said about themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# recall(subject, limit) -> [{"text": ..., "source": ...}]; None whenever
# memory is absent, locked or sealed, which a family must read as "no
# memory to lean on" rather than as an empty memory.
Recall = Callable[[str, int], list[dict]]

# A reading says at most this much, whatever the density preference asks
# for: a card is a glance, and the button carries the deep version.
MAX_ELEMENTS = 4


@dataclass(frozen=True)
class InsightContext:
    """Everything a family may consult, resolved once per payload."""

    preferences: dict
    now: float
    recall: Recall | None = None
    memory_note: str = ""
    # The next meeting, when the calendar is readable: {"title", "time",
    # "meta", "prep"} or None. Shared so a card about research can be
    # about *this* meeting without reaching for the calendar itself.
    agenda: dict | None = None
    agenda_note: str = ""
    # The desk's nameable things (spec 023's connect step): org and
    # person entities with their names and fact counts, the agenda's
    # event clause attached where it names one. Empty whenever memory is
    # absent, locked or sealed, so a directly built context behaves as
    # before. The connect matcher is a pure function of this and a
    # card's rows; a family consults it and nothing else.
    desk_names: tuple = ()

    @property
    def density(self) -> str:
        """The insight density preference, or \"normal\" when none valid is
        stored: every family trims to this, so one setting moves the whole
        dashboard."""
        value = str(self.preferences.get("insightDensity") or "normal")
        return value if value in ("quiet", "normal", "chatty") else "normal"

    @property
    def max_elements(self) -> int:
        """How many elements a reading may add: one when quiet, three when
        normal, four when chatty."""
        return {"quiet": 1, "normal": 3, "chatty": MAX_ELEMENTS}[self.density]


@dataclass
class InsightReading:
    """What one family made of one card.

    `elements` are appended to the card's own payload elements, so they
    render through the same ElementView chain as everything else. `press`
    is the offer of a model call (label + route + the disclosure of what
    would be sent); nothing is spent until the user presses it.

    `used` and `inputs` are the reading's receipts: the preference keys
    whose stored value shaped it, and the sources it consulted. They name
    keys and sources only, never a value, so a receipt cannot leak a focus
    list into a screenshot.
    """

    elements: list[dict] = field(default_factory=list)
    # Row indices the reading would select if the user pressed it, in the
    # workbook's own row numbering. Empty for every family but the workbook.
    suggested: tuple[int, ...] = ()
    press: dict | None = None
    note: str = ""
    status: str = ""
    # The preference keys whose stored value shaped this reading, and the
    # inputs it consulted, in the order it consulted them. Appended after
    # the fields above so a positional construction still says what it did.
    used: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    # How many remembered notes the reading drew on. A name cannot carry a
    # number, and the card's line says "memory (2 notes)"; zero means there
    # is nothing to count.
    memory_notes: int = 0


class InsightError(Exception):
    """A refused reading or draft request. The code is stable and the
    message is written for the person reading it, never for a log."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        """Carry the stable code a client branches on, the sentence written
        for the person reading it, and the HTTP status to answer with."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
