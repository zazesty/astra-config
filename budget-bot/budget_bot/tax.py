"""Tax book. A sticky character overlay on the coach ledger.

Coach categories stay what auto-review wrote. This file is the tax
character. ``accounting/tax-classifications.json`` (mode 600, never git)
wins over auto-review and over ``counts_as_spend``.

Characters answer two questions: taxable income? and does it move the hardcap?

Asset register is deferred. Do not invent basis from a bankruptcy schedule.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .models import AlertEvent, Transaction

# character -> taxable, deductible, counts_as_spend, counts_as_income
# "gift" is a gift received. A gift he made is "deductible" when it is one.
CHARACTER_DEFAULTS: dict[str, dict[str, bool]] = {
    "return_of_capital": {
        "taxable": False,
        "deductible": False,
        "counts_as_spend": False,
        "counts_as_income": False,
    },
    "wages": {
        "taxable": True,
        "deductible": False,
        "counts_as_spend": False,
        "counts_as_income": True,
    },
    "other_taxable_income": {
        "taxable": True,
        "deductible": False,
        "counts_as_spend": False,
        "counts_as_income": True,
    },
    "refund": {
        "taxable": False,
        "deductible": False,
        "counts_as_spend": True,
        "counts_as_income": False,
    },
    "transfer": {
        "taxable": False,
        "deductible": False,
        "counts_as_spend": False,
        "counts_as_income": False,
    },
    "gift": {
        "taxable": False,
        "deductible": False,
        "counts_as_spend": False,
        "counts_as_income": False,
    },
    "deductible": {
        "taxable": False,
        "deductible": True,
        "counts_as_spend": True,
        "counts_as_income": False,
    },
    "nondeductible_personal": {
        "taxable": False,
        "deductible": False,
        "counts_as_spend": True,
        "counts_as_income": False,
    },
}

# Source holes. October 2026 NorCal is not a hole: one hand-added Netflix.
COVERAGE_GAPS: tuple[dict[str, str], ...] = (
    {
        "start": "2026-06-25",
        "end": "2026-06-30",
        "institution": "norcal",
        "note": (
            "NorCal 2026-06-25 through 2026-06-30 is missing. "
            "The workbook ends 2026-06-24 and the July statement starts 2026-07-01."
        ),
    },
)

COVERAGE_NOTES: tuple[dict[str, str], ...] = (
    {
        "start": "2026-10-01",
        "end": "2026-10-31",
        "institution": "norcal",
        "note": (
            "NorCal October 2026 is complete without a statement: "
            "one hand-added Netflix charge on 2026-10-05. Nothing else posted."
        ),
    },
)

EMAIL_INFLOW_CAP = 12
_CACHE: tuple[tuple[str, int], "Book"] | None = None


@dataclass(frozen=True)
class Ruling:
    txn_id: str
    character: str
    taxable: bool
    deductible: bool
    counts_as_spend: bool
    counts_as_income: bool
    note: str = ""
    date: str = ""
    amount_cents: int | None = None


@dataclass
class Book:
    rulings: dict[str, Ruling] = field(default_factory=dict)
    error: str | None = None


def classifications_path():
    from .config import state_dir

    return state_dir() / "accounting" / "tax-classifications.json"


def load_book() -> Book:
    """Parse the overlay. A broken file yields no rulings and an error.

    Spend math then falls through to the coach rules. The quarterly mail
    reports the error instead of a false clean book.
    """
    global _CACHE
    path = classifications_path()
    key_path = str(path)
    if not path.is_file():
        book = Book()
        _CACHE = ((key_path, -1), book)
        return book
    mtime = path.stat().st_mtime_ns
    if _CACHE is not None and _CACHE[0] == (key_path, mtime):
        return _CACHE[1]
    try:
        book = _parse(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        book = Book(error=f"tax classifications unreadable: {exc}")
    _CACHE = ((key_path, mtime), book)
    return book


def ruling_for(txn_id: str) -> Ruling | None:
    return load_book().rulings.get(txn_id)


def is_locked(txn_id: str) -> bool:
    return ruling_for(txn_id) is not None


def pin_locked(txn: Transaction, ruling: Ruling) -> None:
    """Keep a non-spend ruling out of the hardcap and out of bill matching.

    Coach category is left alone. A transfer character is a transfer.
    A spend character (refund, deductible, personal) is not force-excluded:
    the ruling itself decides the hardcap.
    """
    if ruling.character == "transfer":
        txn.transfer = True
        txn.excluded = True
        return
    if not ruling.counts_as_spend:
        txn.excluded = True
        txn.transfer = False


def prior_quarter(as_of: date) -> tuple[int, int, date, date]:
    """Quarter that closed before the quarter containing ``as_of``."""
    q_start_month = ((as_of.month - 1) // 3) * 3 + 1
    current_start = date(as_of.year, q_start_month, 1)
    end = current_start - timedelta(days=1)
    start_month = ((end.month - 1) // 3) * 3 + 1
    start = date(end.year, start_month, 1)
    quarter = ((end.month - 1) // 3) + 1
    return end.year, quarter, start, end


def in_quarterly_window(as_of: date) -> bool:
    """First 7 days of Jan, Apr, Jul, Oct. Same grace as the leftover mail."""
    return as_of.month in (1, 4, 7, 10) and as_of.day <= 7


def tie_out(
    txns: list[Transaction],
    start: date,
    end: date,
) -> dict[str, Any]:
    """One character per posted dollar in [start, end], or a gap.

    Excluded and transfer rows with no ruling stay out of the book
    (Plaid twins, internal moves). A ruling on an excluded row still counts.
    """
    book = load_book()
    ids_in_ledger = {t.id for t in txns}
    characters: dict[str, dict[str, Any]] = {}
    unclassified = {"n": 0, "in_cents": 0, "out_cents": 0}
    parked = {"n": 0, "in_cents": 0, "out_cents": 0}
    pending = {"n": 0}
    inflows: list[dict[str, Any]] = []

    for txn in txns:
        posted = _parse_ymd(txn.date)
        if posted is None or posted < start or posted > end:
            continue
        ruling = book.rulings.get(txn.id)
        if txn.pending and ruling is None:
            pending["n"] += 1
            continue
        if ruling is not None:
            bucket = characters.setdefault(
                ruling.character,
                {
                    "n": 0,
                    "in_cents": 0,
                    "out_cents": 0,
                    "taxable": ruling.taxable,
                    "deductible": ruling.deductible,
                    "counts_as_spend": ruling.counts_as_spend,
                    "counts_as_income": ruling.counts_as_income,
                    "rows": [],
                },
            )
            bucket["n"] += 1
            _add_money(bucket, txn.amount_cents)
            if len(bucket["rows"]) < 8:
                bucket["rows"].append(_row(txn, ruling.note))
            continue
        if txn.excluded or txn.transfer:
            parked["n"] += 1
            _add_money(parked, txn.amount_cents)
            continue
        unclassified["n"] += 1
        _add_money(unclassified, txn.amount_cents)
        if txn.amount_cents < 0:
            inflows.append(_row(txn, ""))

    inflows.sort(key=lambda row: (row["date"], row["label"]))
    orphans = []
    for ruling in book.rulings.values():
        if ruling.txn_id in ids_in_ledger:
            continue
        rd = _parse_ymd(ruling.date) if ruling.date else None
        if rd is not None and start <= rd <= end:
            orphans.append(ruling.txn_id)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "error": book.error,
        "characters": characters,
        "unclassified": unclassified,
        "excluded_or_transfer": parked,
        "pending_unclassified": pending,
        "gaps": _overlaps(COVERAGE_GAPS, start, end),
        "notes": _overlaps(COVERAGE_NOTES, start, end),
        "year_gaps": _overlaps(
            COVERAGE_GAPS, date(start.year, 1, 1), date(start.year, 12, 31)
        ),
        "inflows": inflows,
        "orphan_rulings": orphans,
    }


def review_event(
    txns: list[Transaction], as_of: date, *, force: bool = False
) -> AlertEvent | None:
    """Prior-quarter review. None outside the first week of a quarter."""
    if not force and not in_quarterly_window(as_of):
        return None
    year, quarter, start, end = prior_quarter(as_of)
    report = tie_out(txns, start, end)
    label = f"{year} Q{quarter}"
    return AlertEvent(
        kind="tax_review",
        subject=f"Budget Bot tax review {label}",
        body=_email_body(label, report),
        key=f"tax_review|{year}Q{quarter}",
        payload={"year": year, "quarter": quarter, "report": _public_report(report)},
    )


def _parse(text: str) -> Book:
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("tax file must be an object")
    version = raw.get("version")
    if version != 1:
        raise ValueError(f"tax file version {version} is not 1")
    items = raw.get("items")
    if not isinstance(items, list):
        raise ValueError("tax file items must be a list")
    rulings: dict[str, Ruling] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("tax item must be an object")
        txn_id = str(item.get("txn_id") or "").strip()
        if not txn_id:
            raise ValueError("tax item missing txn_id")
        if txn_id in rulings:
            raise ValueError(f"duplicate tax ruling {txn_id}")
        character = str(item.get("character") or "").strip()
        defaults = CHARACTER_DEFAULTS.get(character)
        if defaults is None:
            known = ", ".join(sorted(CHARACTER_DEFAULTS))
            raise ValueError(f"unknown tax character {character!r} (known: {known})")
        rulings[txn_id] = Ruling(
            txn_id=txn_id,
            character=character,
            taxable=_flag(item, "taxable", defaults["taxable"]),
            deductible=_flag(item, "deductible", defaults["deductible"]),
            counts_as_spend=_flag(item, "counts_as_spend", defaults["counts_as_spend"]),
            counts_as_income=_flag(item, "counts_as_income", defaults["counts_as_income"]),
            note=str(item.get("note") or "").strip(),
            date=str(item.get("date") or "").strip(),
            amount_cents=_optional_int(item.get("amount_cents")),
        )
    return Book(rulings=rulings)


def _flag(item: dict[str, Any], key: str, default: bool) -> bool:
    if key not in item:
        return default
    return bool(item[key])


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _parse_ymd(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def _add_money(bucket: dict[str, Any], amount_cents: int) -> None:
    if amount_cents < 0:
        bucket["in_cents"] += -amount_cents
    elif amount_cents > 0:
        bucket["out_cents"] += amount_cents


def _row(txn: Transaction, note: str) -> dict[str, Any]:
    return {
        "date": txn.date,
        "cents": txn.amount_cents,
        "label": _short_label(txn),
        "note": _first_sentence(note),
    }


def _short_label(txn: Transaction) -> str:
    raw = (txn.merchant_name or txn.name or "").strip()
    raw = re.sub(r"\s+", " ", raw)
    raw = raw.split("%%")[0].strip(" -")
    return raw[:60]


def _first_sentence(note: str) -> str:
    text = re.sub(r"\s+", " ", note).strip()
    if not text:
        return ""
    stop = text.find(". ")
    if stop == -1:
        return text[:180]
    return text[: stop + 1]


def _overlaps(
    rows: tuple[dict[str, str], ...], start: date, end: date
) -> list[dict[str, str]]:
    out = []
    for row in rows:
        rs = date.fromisoformat(row["start"])
        re_ = date.fromisoformat(row["end"])
        if rs <= end and re_ >= start:
            out.append(dict(row))
    return out


def _dollars(cents: int) -> str:
    n = abs(int(cents))
    return f"${n // 100:,}.{n % 100:02d}"


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


def _email_body(label: str, report: dict[str, Any]) -> str:
    start = date.fromisoformat(report["start"])
    end = date.fromisoformat(report["end"])
    lines = [
        f"Budget Bot tax review {label}",
        f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}, {end.year}",
        "",
    ]
    if report.get("error"):
        lines.append(str(report["error"]))
        lines.append("No character totals until that file parses.")
        lines.append("")
    else:
        lines.append("Classified")
        characters = report["characters"]
        if not characters:
            lines.append("  None this quarter.")
        for name in sorted(characters):
            bucket = characters[name]
            money = []
            if bucket["in_cents"]:
                money.append(f"{_dollars(bucket['in_cents'])} in")
            if bucket["out_cents"]:
                money.append(f"{_dollars(bucket['out_cents'])} out")
            shown = ", ".join(money) or "$0.00"
            n = bucket["n"]
            noun = "row" if n == 1 else "rows"
            lines.append(
                f"  {name.replace('_', ' ')}: {n} {noun}, {shown}. "
                f"Taxable: {_yn(bucket['taxable'])}. "
                f"Hardcap: {_yn(bucket['counts_as_spend'])}."
            )
            if sum(b["n"] for b in characters.values()) <= 8:
                for row in bucket["rows"]:
                    way = "in" if row["cents"] < 0 else "out"
                    extra = f" {row['note']}" if row["note"] else ""
                    lines.append(
                        f"    {row['date']} {_dollars(row['cents'])} {way}.{extra}"
                    )
        lines.append("")

    unclassified = report["unclassified"]
    lines.append(
        f"Unclassified posted rows: {unclassified['n']}"
    )
    lines.append(
        f"  in {_dollars(unclassified['in_cents'])}"
    )
    lines.append(
        f"  out {_dollars(unclassified['out_cents'])}"
    )
    lines.append("")
    lines.append("Money in still unlabeled:")
    sample = report["inflows"][:EMAIL_INFLOW_CAP]
    if not sample:
        lines.append("  None.")
    for row in sample:
        lines.append(f"  {row['date']} {_dollars(row['cents'])}  {row['label']}")
    omitted = len(report["inflows"]) - len(sample)
    if omitted > 0:
        lines.append(f"  and {omitted} more")
    lines.append("")
    parked = report["excluded_or_transfer"]
    lines.append(
        "Out of the tax book (transfer or excluded, no character): "
        f"{parked['n']}"
    )
    if report["orphan_rulings"]:
        lines.append(
            "Rulings with no ledger row: " + ", ".join(report["orphan_rulings"])
        )
    year_gaps = report.get("year_gaps") or []
    if year_gaps:
        lines.append("")
        lines.append(f"Still open in {start.year}")
        for gap in year_gaps:
            lines.append(f"  {gap['note']}")
    if report["notes"]:
        lines.append("")
        lines.append("Coverage")
        for note in report["notes"]:
            lines.append(f"  {note['note']}")
    lines.append("")
    lines.append(
        "Pace and Pushover stay on the coach book. This mail is the quarterly review."
    )
    lines.append("")
    return "\n".join(lines)


def _public_report(report: dict[str, Any]) -> dict[str, Any]:
    """Counts for the CLI. Drops per-row notes so a log stays small."""
    characters = {}
    for name, bucket in report["characters"].items():
        characters[name] = {
            "n": bucket["n"],
            "in_cents": bucket["in_cents"],
            "out_cents": bucket["out_cents"],
            "taxable": bucket["taxable"],
            "counts_as_spend": bucket["counts_as_spend"],
        }
    return {
        "start": report["start"],
        "end": report["end"],
        "error": report["error"],
        "characters": characters,
        "unclassified_n": report["unclassified"]["n"],
        "unclassified_in_cents": report["unclassified"]["in_cents"],
        "unclassified_out_cents": report["unclassified"]["out_cents"],
        "excluded_or_transfer_n": report["excluded_or_transfer"]["n"],
        "inflow_n": len(report["inflows"]),
        "gaps": [g["note"] for g in report["gaps"]],
        "year_gaps": [g["note"] for g in report["year_gaps"]],
        "orphan_rulings": report["orphan_rulings"],
    }
