"""The workbook family: what the selected rows are hiding.

The card already carries the total of the rows the user picked. This family
adds the relationship that total cannot show (a movement in time, a link
between two columns, a group carrying the range, a near-tie, or one row far
from the rest) and names the chart that would carry it. The maths lives in
`relations`; this module decides what fits on one card.

Two findings at most, then one sentence about the chart. Below the
thresholds the card says so in one line instead of guessing, because a
reading that cannot be computed is still a reading.
"""

from __future__ import annotations

from agentdesk.insight import insight_connect
from agentdesk.insight import press as press_module
from agentdesk.insight import relations
from agentdesk.insight.types import InsightContext, InsightReading

MAX_FINDINGS = 2
MAX_SUGGESTIONS = 5
PRESS = {"label": "Explain this range", "route": "/api/insight/draft", "kind": "explain-range"}

_CHART_PREFERENCES = ("bar", "line", "table")


def derive(card: dict, payload: dict, context: InsightContext) -> InsightReading:
    """One reading of the tracker table, or one honest sentence."""
    tracker = payload.get("tracker")
    if not isinstance(tracker, dict):
        return _note("This workbook card has no rows to read yet.")
    table = relations.read_table(tracker.get("table"))
    if table is None:
        return _note("This workbook card carries no table yet, so there is nothing to compare.")

    numbers = [column for column in table.columns if column.kind == "number"]
    dated = [column for column in table.columns if column.kind == "date"]
    if not numbers:
        return _note(
            "Nothing in this range reads as a number, so there is no relationship to state."
        )

    found = relations.findings(table, label_column=_label_column(tracker))
    if not found:
        # The maths decides first: a single numeric column can still carry a
        # share against a label column, so the note explains the specific
        # reason only after every detector has refused.
        if len(numbers) == 1 and not dated:
            return _note(
                "Only one column here reads as a number, so there is nothing to hold it against."
            )
        return _note(
            "This range is too small or too flat to support a claim, so the card says nothing more."
        )

    elements = [
        {"kind": "text", "label": finding.label, "text": finding.text}
        for finding in found[:MAX_FINDINGS]
    ]
    preference = _chart_preference(context)
    _shape, sentence = relations.chart_choice(found[0], preference)
    elements.append({"kind": "text", "label": "Chart", "text": sentence})
    if context.density == "chatty":
        checklist = _checklist(table, found)
        if checklist is not None:
            elements.append(checklist)
    suggested = tuple(
        row for row in found[0].suggested if isinstance(row, int)
    )[:5]
    # The connect step (spec 023): what the rows' names already sit in the
    # desk's memory and agenda. A block on the card, never an element, and
    # its receipts say so: no match leaves the reading exactly as it was.
    line, notes = insight_connect.connect_line(_row_labels(table, tracker), context.desk_names)
    # The press carries the sentence it would explain, so a client never has
    # to re-derive which finding the button belonged to.
    press = dict(PRESS, text=found[0].text[: press_module.MAX_FINDING])
    # The table is the only thing this reading read, and the chart setting is
    # a receipt only once it is off auto: that is when it changed the sentence.
    used = ("chart",) if preference != "auto" else ()
    if line:
        return InsightReading(
            elements=elements,
            suggested=suggested,
            press=press,
            note=line,
            used=used,
            inputs=("rows", "memory", "calendar"),
            memory_notes=notes,
        )
    return InsightReading(
        elements=elements,
        suggested=suggested,
        press=press,
        used=used,
        inputs=("rows",),
    )


def _row_labels(table: relations.Table, tracker: dict) -> list[str]:
    """Every row's label text, in the card's order: the tracker's chosen
    label column when it chose one, else each row's first non-numeric
    cell."""
    label = _label_column(tracker)
    column = None
    if label:
        column = next((c for c in table.columns
                       if c.kind != "number" and c.name == label), None)
    labels = []
    for index, row in enumerate(table.rows):
        if column is not None:
            labels.append(row[column.index])
            continue
        labels.append(_row_label(table, index))
    return labels


def _note(message: str) -> InsightReading:
    """A reading that says one honest sentence and shows nothing."""
    return InsightReading(note=message)


def _label_column(tracker: dict) -> str | None:
    """The column the tracker chose to name rows by, when it chose one."""
    name = tracker.get("labelColumn")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _chart_preference(context: InsightContext) -> str:
    """The chart setting, or \"auto\" when none of the three shapes is
    stored: only a stated shape is a receipt, auto is the desk's own
    judgement."""
    value = context.preferences.get("chart")
    return value if value in _CHART_PREFERENCES else "auto"


def _checklist(table: relations.Table, found: list[relations.Finding]) -> dict | None:
    """The rows behind the findings, offered as a checklist."""
    indexes: list[int] = []
    for finding in found:
        for index in finding.suggested:
            if 0 <= index < len(table.rows) and index not in indexes:
                indexes.append(index)
    if not indexes:
        return None
    items = []
    for index in sorted(indexes)[:MAX_SUGGESTIONS]:
        items.append(
            {
                "text": f"Row {index + 1}: {_row_label(table, index)}",
                "done": False,
                "due": "",
            }
        )
    return {"kind": "checklist", "title": "Rows worth selecting", "items": items}


def _row_label(table: relations.Table, index: int) -> str:
    """A row's first non-numeric cell, capped at 40 characters, falling
    back to \"row N\" when the row is nothing but numbers."""
    for column in table.columns:
        if column.kind == "number":
            continue
        cell = table.rows[index][column.index].strip()
        if cell:
            return cell[:40]
    return f"row {index + 1}"
