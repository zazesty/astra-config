"""Keep the Astra buy-queue table and config.buy_queue on the same rows.

The table in /root/memory/budget-bot-buy-queue.md is the list a chat edit
touches. config.buy_queue is what the matcher runs. Each Budget Bot pass
copies whichever side changed onto the other. A pulled row leaves the table.
An added or repriced table row lands in config. Blank merchant stays on the
list and does not match. Costco asks instead of auto-clearing.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .buy_queue import COSTCO_MATCH, is_costco_merchant, replace_buy_queue
from .config import state_dir

QUEUE_FACT = Path("/root/memory/budget-bot-buy-queue.md")
BUDGET_FACT = Path("/root/memory/budget-bot.md")
PT = ZoneInfo("America/Los_Angeles")

# Display names that must keep their existing config ids.
_ALIASES = {
    "maro toe socks 6pk": "maro-toe-socks",
    "rice cooker": "rice-cooker",
    "beard trimmer (gift, papa)": "norelco-bt7670",
    "us mobile annual": "us-mobile-annual",
    "supergrok annual": "supergrok-annual",
    "lenovo x1 carbon gen 9": "x1-carbon-g9",
    "iphone 16 pro": "iphone-16-pro",
}


def _sync_path() -> Path:
    return state_dir() / "buy_queue_sync.json"


def _fp(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _norm_merchant(merchant: str) -> str:
    return re.sub(r"\s+", " ", (merchant or "").strip().lower())


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (slug or "item")[:48]


def _parse_dollars(text: str) -> int | None:
    cleaned = text.replace("$", "").replace(",", "").replace("~", "").strip()
    if not cleaned:
        return None
    try:
        return int(round(float(cleaned) * 100))
    except ValueError:
        return None


def _split_table_line(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def parse_queue_table(text: str) -> list[dict[str, Any]] | None:
    """Rows from the Order-now table. None when the table is missing or empty."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        cells = _split_table_line(line) if line.strip().startswith("|") else []
        if cells and cells[0].lower() == "rank" and any(c.lower().startswith("item") for c in cells):
            start = i
            break
    if start is None or start + 2 >= len(lines):
        return None
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines[start + 2 :]:
        if not line.strip().startswith("|"):
            break
        cells = _split_table_line(line)
        if len(cells) < 5:
            continue
        _rank, name, price, merchant, *rest = cells
        if not name or name.lower() == "item":
            continue
        cents = _parse_dollars(price)
        if cents is None:
            continue
        notes = " | ".join(rest).strip()
        id_match = re.search(r"\bid:([a-z0-9-]+)", notes, re.I)
        if id_match:
            rid = id_match.group(1).lower()
        else:
            rid = _ALIASES.get(name.lower(), _slug(name))
        if rid in seen:
            continue
        seen.add(rid)
        rows.append(
            {
                "id": rid,
                "name": name,
                "amount_cents": cents,
                "merchant": merchant,
                "notes": notes,
            }
        )
    return rows or None


def _table_fp(rows: list[dict[str, Any]]) -> str:
    return _fp(
        [
            {
                "id": r["id"],
                "name": r["name"],
                "amount_cents": r["amount_cents"],
                "merchant": _norm_merchant(r.get("merchant") or ""),
            }
            for r in sorted(rows, key=lambda r: r["id"])
        ]
    )


def _config_fp(rows: list[dict[str, Any]]) -> str:
    return _fp(
        [
            {
                "id": str(r.get("id") or ""),
                "name": r.get("name") or "",
                "amount_cents": int(r.get("amount_cents") or 0),
                "merchant": _norm_merchant(str(r.get("merchant") or "")),
                "match": str(r.get("match") or ""),
                "confirm": bool(r.get("confirm")),
                "not_before": str(r.get("not_before") or ""),
            }
            for r in rows
            if r.get("id")
        ]
    )


def _today_pt() -> str:
    return datetime.now(PT).date().isoformat()


def _match_for_merchant(merchant: str) -> str:
    parts = [p.strip() for p in re.split(r"\s*/\s*|\s+or\s+", merchant) if p.strip()]
    if not parts:
        return ""
    return "|".join(re.escape(p) for p in parts)


def _row_from_spec(spec: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    merchant = str(spec.get("merchant") or "").strip()
    costco = is_costco_merchant(merchant)
    prev_merchant = _norm_merchant(str((prev or {}).get("merchant") or ""))
    # No stored merchant yet means the row predates this field. Keep its regex.
    same_merchant = prev is not None and (
        not prev_merchant or prev_merchant == _norm_merchant(merchant)
    )
    if prev and same_merchant:
        row = dict(prev)
        row["name"] = spec["name"]
        row["amount_cents"] = int(spec["amount_cents"])
        row["merchant"] = merchant
        if not merchant:
            row["match"] = ""
            row["confirm"] = False
        elif costco:
            row["confirm"] = True
            row["match"] = prev.get("match") or COSTCO_MATCH
            row.setdefault("not_before", _today_pt())
        else:
            row["confirm"] = False
        return row
    row = {
        "amount_cents": int(spec["amount_cents"]),
        "id": spec["id"],
        "match": COSTCO_MATCH if costco else _match_for_merchant(merchant),
        "merchant": merchant,
        "name": spec["name"],
    }
    if costco:
        row["confirm"] = True
        row["not_before"] = str((prev or {}).get("not_before") or _today_pt())
    elif prev and prev.get("not_before") and same_merchant:
        row["not_before"] = prev["not_before"]
    return row


def _apply_specs(
    specs: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    old = {str(r.get("id") or ""): r for r in current if r.get("id")}
    ordered = sorted(specs, key=lambda s: (int(s["amount_cents"]), str(s["name"]).lower()))
    return [_row_from_spec(spec, old.get(spec["id"])) for spec in ordered]


def _same_identity(specs: list[dict[str, Any]], current: list[dict[str, Any]]) -> bool:
    spec_ids = {(s["id"], int(s["amount_cents"])) for s in specs}
    cfg_ids = {
        (str(r.get("id") or ""), int(r.get("amount_cents") or 0))
        for r in current
        if r.get("id")
    }
    return spec_ids == cfg_ids


def _stamp_from_table(
    specs: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """First pass: record merchant and the Costco ask without renaming anything."""
    by_id = {s["id"]: s for s in specs}
    stamped = []
    for row in current:
        rid = str(row.get("id") or "")
        spec = by_id.get(rid)
        if not spec:
            stamped.append(row)
            continue
        nxt = dict(row)
        merchant = str(spec.get("merchant") or "").strip()
        nxt["merchant"] = merchant
        if is_costco_merchant(merchant):
            nxt["confirm"] = True
            nxt["match"] = nxt.get("match") or COSTCO_MATCH
            nxt.setdefault("not_before", _today_pt())
        elif not merchant:
            nxt["match"] = ""
            nxt["confirm"] = False
        else:
            nxt["confirm"] = False
        stamped.append(nxt)
    return stamped


def _load_state() -> dict[str, Any]:
    path = _sync_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(table_fp: str, config_fp: str) -> None:
    path = _sync_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "table_fp": table_fp,
                "config_fp": config_fp,
                "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            indent=2,
        )
        + "\n"
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _seeded_line(rows: list[dict[str, Any]]) -> str:
    parts = []
    ordered = sorted(rows, key=lambda r: (int(r.get("amount_cents") or 0), str(r.get("name") or "")))
    for row in ordered:
        dollars = int(row.get("amount_cents") or 0) // 100
        merchant = str(row.get("merchant") or "no merchant")
        label = str(row.get("name") or row.get("id"))
        suffix = " (ask)" if is_costco_merchant(merchant) or row.get("confirm") else ""
        parts.append(f"{label} ${dollars} @ {merchant}{suffix}")
    body = "; ".join(parts) if parts else "(empty)"
    return f"**Seeded matcher rows (box config, reconciled each pass):** {body}."


def _bump_frontmatter(text: str) -> str:
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not match:
        return text
    block = match.group(1)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _ver(m: re.Match[str]) -> str:
        return f"version: {int(m.group(1)) + 1}"

    block2, n = re.subn(r"^version:\s*(\d+)\s*$", _ver, block, count=1, flags=re.M)
    if n == 0:
        block2 = block + f"\nversion: 1"
    block2, n = re.subn(r"^updated:\s*.*$", f"updated: {now}", block2, count=1, flags=re.M)
    if n == 0:
        block2 += f"\nupdated: {now}"
    return f"---\n{block2}\n---\n" + text[match.end() :]


def _render_table(text: str, specs_by_id: dict[str, dict[str, Any]], cfg_rows: list[dict[str, Any]]) -> str | None:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        cells = _split_table_line(line) if line.strip().startswith("|") else []
        if cells and cells[0].lower() == "rank" and any(c.lower().startswith("item") for c in cells):
            start = i
            break
    if start is None:
        return None
    end = start + 2
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    ordered = sorted(cfg_rows, key=lambda r: (int(r.get("amount_cents") or 0), str(r.get("name") or "").lower()))
    rendered = []
    for n, row in enumerate(ordered, start=1):
        rid = str(row.get("id") or "")
        spec = specs_by_id.get(rid) or {}
        name = spec.get("name") or row.get("name") or rid
        merchant = str(row.get("merchant") or spec.get("merchant") or "")
        notes = str(spec.get("notes") or f"id:{rid}")
        if not re.search(rf"\bid:{re.escape(rid)}\b", notes, re.I):
            notes = f"id:{rid}. {notes}".strip()
        dollars = int(row.get("amount_cents") or 0) / 100
        price = str(int(dollars)) if dollars == int(dollars) else f"{dollars:.2f}"
        rendered.append(f"| {n} | {name} | {price} | {merchant} | {notes} |")
    new_lines = lines[: start + 2] + rendered + lines[end:]
    return "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")


def _replace_seeded(text: str, line: str) -> str:
    out, n = re.subn(
        r"^\*\*Seeded matcher rows.*$",
        line,
        text,
        count=1,
        flags=re.M,
    )
    return out if n else text


def _write_fact(path: Path, text: str) -> None:
    path.write_text(text if text.endswith("\n") else text + "\n")


def _regen_indexes(memory_dir: Path) -> None:
    script = (
        "import { regenerateIndexes } from './build/memory.js'; "
        f"await regenerateIndexes({json.dumps(str(memory_dir))});"
    )
    subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd="/root/grok-mcp",
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def reconcile_buy_queue(
    cfg: dict[str, Any],
    *,
    queue_fact: Path | None = None,
    budget_fact: Path | None = None,
    regen: bool | None = None,
) -> dict[str, Any]:
    """Copy a changed table onto config, or a changed config back onto the table."""
    queue_path = queue_fact or QUEUE_FACT
    budget_path = budget_fact or BUDGET_FACT
    if not queue_path.is_file():
        return cfg
    text = queue_path.read_text()
    specs = parse_queue_table(text)
    if not specs:
        return cfg
    current = [r for r in (cfg.get("buy_queue") or []) if isinstance(r, dict)]
    table_fp = _table_fp(specs)
    config_fp = _config_fp(current)
    state = _load_state()
    prev_table = state.get("table_fp")
    prev_config = state.get("config_fp")

    if prev_table is None or prev_config is None:
        if _same_identity(specs, current):
            stamped = _stamp_from_table(specs, current)
            if _config_fp(stamped) != config_fp:
                replace_buy_queue(cfg, stamped)
                config_fp = _config_fp(stamped)
            else:
                cfg["buy_queue"] = stamped
            _save_state(table_fp, config_fp)
        else:
            merged = _apply_specs(specs, current)
            replace_buy_queue(cfg, merged)
            _save_state(table_fp, _config_fp(merged))
        return cfg

    if table_fp == prev_table and config_fp == prev_config:
        return cfg

    if table_fp != prev_table and config_fp == prev_config:
        merged = _apply_specs(specs, current)
        replace_buy_queue(cfg, merged)
        cfg["buy_queue"] = merged
        _write_table_from_config(
            cfg_rows=merged,
            specs=specs,
            queue_path=queue_path,
            queue_text=text,
            budget_path=budget_path,
            regen=regen,
        )
        rewritten = parse_queue_table(queue_path.read_text()) or specs
        _save_state(_table_fp(rewritten), _config_fp(merged))
        return cfg

    # Config changed (a pull, a yes, or a box edit). Refresh the table from it.
    # A table edit in the same pass already won above, because that branch
    # runs only when config still matches the previous fingerprint.
    _write_table_from_config(
        cfg_rows=list(cfg.get("buy_queue") or []),
        specs=specs,
        queue_path=queue_path,
        queue_text=text,
        budget_path=budget_path,
        regen=regen,
    )
    rewritten = parse_queue_table(queue_path.read_text()) or specs
    _save_state(_table_fp(rewritten), _config_fp(list(cfg.get("buy_queue") or [])))
    return cfg


def _write_table_from_config(
    *,
    cfg_rows: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    queue_path: Path,
    queue_text: str,
    budget_path: Path,
    regen: bool | None,
) -> None:
    by_id = {s["id"]: s for s in specs}
    rendered = _render_table(queue_text, by_id, cfg_rows)
    seeded = _seeded_line(cfg_rows)
    wrote = False
    if rendered is not None and rendered != queue_text:
        _write_fact(queue_path, _bump_frontmatter(_replace_seeded(rendered, seeded)))
        wrote = True
    elif _replace_seeded(queue_text, seeded) != queue_text:
        _write_fact(queue_path, _bump_frontmatter(_replace_seeded(queue_text, seeded)))
        wrote = True
    if budget_path.is_file():
        budget_text = budget_path.read_text()
        updated = _replace_seeded(budget_text, seeded)
        if updated != budget_text:
            _write_fact(budget_path, _bump_frontmatter(updated))
            wrote = True
    do_regen = wrote if regen is None else regen
    if do_regen and wrote and queue_path.parent == QUEUE_FACT.parent:
        _regen_indexes(queue_path.parent)
