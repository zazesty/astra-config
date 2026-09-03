"""Import Consolidated 1H xlsx (inlineStr sheets) into Hermes Transaction list.

Excel amount convention: negative = outflow (spend), positive = inflow.
Hermes amount_cents: positive = spend toward hardcap.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Transaction

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
# Excel serial date epoch (Windows): 1899-12-30
_EXCEL_EPOCH = date(1899, 12, 30)


def excel_serial_to_date(n: float | int | str) -> str:
    serial = int(float(n))
    return (_EXCEL_EPOCH + timedelta(days=serial)).isoformat()


def _cell_text(c: ET.Element) -> str | None:
    t = c.get("t")
    if t == "inlineStr":
        texts = [
            (x.text or "")
            for x in c.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        ]
        return "".join(texts)
    v = c.find("m:v", NS)
    if v is None:
        return None
    return v.text


def _row_cells(row: ET.Element) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for c in row.findall("m:c", NS):
        ref = c.get("r") or ""
        col = re.match(r"([A-Z]+)", ref)
        if not col:
            continue
        out[col.group(1)] = _cell_text(c)
    return out


def read_sheet_rows(z: zipfile.ZipFile, sheet_path: str) -> list[dict[str, str | None]]:
    root = ET.fromstring(z.read(sheet_path))
    rows_out: list[dict[str, str | None]] = []
    for row in root.findall("m:sheetData/m:row", NS):
        rows_out.append(_row_cells(row))
    return rows_out


def _trim_address_tail(tail: str) -> str:
    """Keep merchant-ish head; drop street numbers / city address noise."""
    tail = tail.strip()
    # stop at first token that looks like a street number (3+ digits) after first word
    parts = tail.split()
    kept: list[str] = []
    for i, p in enumerate(parts):
        if i > 0 and re.match(r"^\d{3,}", p):
            break
        if i > 0 and p.upper() in {"STREET", "ST", "AVE", "AVENUE", "BLVD", "RD", "ROAD", "PKWY", "FL", "SUITE"}:
            # drop this and rest unless it's part of merchant like "11 E 44TH"
            if i >= 2:
                break
        kept.append(p)
        if len(kept) >= 6:
            break
    return " ".join(kept).strip(" -")[:80] or tail[:80]


def extract_mcc(desc: str) -> str | None:
    """4-digit MCC after MasterMoney REF# (before the dash + merchant).

    Example: `REF#: 5365DJLCQTTI 5542 - CHEVRON ...` → `5542`.
    """
    m = re.search(r"REF#:\s*\S+\s+(\d{4})\s*-", desc, re.I)
    return m.group(1) if m else None


def guess_merchant(desc: str) -> str:
    d = desc.strip()
    # CU debit with optional REF# + optional MCC: "... REF#: ABC 5542 - MERCHANT"
    m = re.search(
        r"MasterMoney Card(?:\s+REF#:\s*\S+(?:\s+\d{4})?)?\s*-\s*(.+)$",
        d,
        re.I,
    )
    if m:
        return _trim_address_tail(m.group(1))
    # POS #123456 - MERCHANT ADDR
    m = re.search(r"POS\s*#\d+\s*-\s*(.+)$", d, re.I)
    if m:
        return _trim_address_tail(m.group(1))
    # ACH / bill pay "CO: NAME"
    m = re.search(r"CO:\s*([A-Z0-9 .&'-]+)", d, re.I)
    if m:
        return m.group(1).strip()[:80]
    # PayPal-style "Merchant - Payment type"
    if " - " in d:
        head = d.split(" - ", 1)[0].strip()
        head = re.sub(
            r"^(Recurring\s+)?Withdrawal(\s+Debit Card)?\s*",
            "",
            head,
            flags=re.I,
        ).strip()
        # skip opaque placeholders that hid the real merchant
        if re.match(r"^(POS\s*#\d+|MASTERMONEY CARD REF#.*)$", head, re.I):
            tail = d.split(" - ", 1)[1].strip()
            if tail:
                return _trim_address_tail(tail)
        if head and head.lower() not in {"withdrawal", "debit card"}:
            return head[:80]
    return d[:80]


def source_to_institution(source: str) -> str:
    s = (source or "").lower()
    if "paypal" in s:
        return "paypal"
    if "credit" in s or "union" in s:
        return "norcal"
    return s.replace(" ", "_")[:32] or "unknown"


def make_id(date_s: str, amount_cents: int, name: str, account: str, source: str) -> str:
    raw = f"{date_s}|{amount_cents}|{name}|{account}|{source}"
    return "imp-" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def xlsx_to_transactions(path: Path, sheet: str = "All_Transactions") -> list[Transaction]:
    z = zipfile.ZipFile(path)
    # map sheet name → path via workbook
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {
        r.get("Id"): r.get("Target", "").lstrip("/")
        for r in rels
        if r.get("Id")
    }
    # Targets may be "xl/worksheets/..." or "/xl/..."
    def norm(t: str) -> str:
        t = t.lstrip("/")
        if not t.startswith("xl/"):
            t = "xl/" + t if not t.startswith("worksheets") else "xl/" + t
        if t.startswith("xl/xl/"):
            t = t[3:]
        return t

    sheet_path = None
    for s in wb.findall("m:sheets/m:sheet", NS):
        if s.get("name") == sheet:
            rid = s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rid_to_target.get(rid or "", "")
            # rels targets often "worksheets/sheet1.xml"
            if target.startswith("worksheets/"):
                sheet_path = "xl/" + target
            elif target.startswith("/xl/"):
                sheet_path = target[1:]
            elif target.startswith("xl/"):
                sheet_path = target
            else:
                sheet_path = "xl/worksheets/" + target.split("/")[-1]
            break
    if not sheet_path:
        sheet_path = "xl/worksheets/sheet1.xml"

    rows = read_sheet_rows(z, sheet_path)
    if not rows:
        return []
    # skip header
    data_rows = rows[1:]
    txns: list[Transaction] = []
    for r in data_rows:
        raw_date = r.get("A")
        raw_amt = r.get("B")
        desc = (r.get("C") or "").strip()
        account = (r.get("D") or "").strip()
        source = (r.get("E") or "").strip()
        if raw_date is None or raw_amt is None or not desc:
            continue
        try:
            date_s = excel_serial_to_date(raw_date)
            # Excel dollars: negative outflow → Hermes positive spend
            dollars = float(raw_amt)
            amount_cents = int(round(-dollars * 100))  # invert sign
        except Exception:
            continue
        merchant = guess_merchant(desc)
        inst = source_to_institution(source)
        tid = make_id(date_s, amount_cents, desc, account, source)
        txns.append(
            Transaction(
                id=tid,
                date=date_s,
                amount_cents=amount_cents,
                name=desc[:200],
                merchant_name=merchant,
                category="Misc / Other",
                institution=inst,
                account_id=account,
            )
        )
    return txns
