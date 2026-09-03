"""Import 1st Nor Cal (and similar) text-extracted account statement PDFs.

Uses `pdftotext -layout` output or raw text. Excel convention elsewhere differs:
  PDF amounts: negative = outflow, positive = inflow.
  Hermes amount_cents: positive = spend toward hardcap.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from .models import Transaction
from .transfers import looks_like_transfer

# Line: 07/01/2026   Description ...  -10.00   or  180.00
_LINE_RE = re.compile(
    r"^\s*(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-?\$?\d{1,3}(?:,\d{3})*\.\d{2})\s*$"
)
_CONT_RE = re.compile(r"^\s{4,}\S")  # continuation of description
_SKIP_START = re.compile(
    r"(?i)^(starting balance|ending balance|dividends paid|id\s*:|"
    r"\d+\s+total deposits|account statement|page:|po box|your account|"
    r"date\s+transaction|atm activity|withdrawal\s*$|deposit\s*$|"
    r"\d+\s+atm withdrawals)"
)
# 3-column "ATM ACTIVITY AT A GLANCE" recap: date amount date amount date amount
_RECAP_LINE = re.compile(
    r"^\s*\d{2}/\d{2}/\d{4}\s+\$?\d{1,3}(?:,\d{3})*\.\d{2}\s+\d{2}/\d{2}/\d{4}\b"
)
_REAL_TXN_DESC = re.compile(
    r"^(Recurring\s+)?(Withdrawal|Deposit|Dividend)\b", re.I
)
_ATM_GLANCE = re.compile(r"(?i)ATM ACTIVITY AT A GLANCE")
_ACCOUNT_SECTION = re.compile(r"^\s*(SAVINGS|CHECKING|MONEY MARKET)\s*$")


def _mdy_to_iso(s: str) -> str:
    m, d, y = s.split("/")
    return f"{y}-{m.zfill(2)}-{d.zfill(2)}"


def _parse_amount(s: str) -> int:
    """Return cents in Hermes convention: positive = outflow/spend."""
    clean = s.replace("$", "").replace(",", "").strip()
    val = float(clean)
    # PDF: negative = withdrawal; Hermes: positive = spend
    return int(round(-val * 100))


def _is_transfer_desc(desc: str) -> bool:
    u = desc.upper()
    # PayPal ↔ CU, Venmo bridge often peer; treat PayPal ACH as transfer
    if "PAYPAL" in u and ("DEPOSIT" in u or "WITHDRAWAL" in u or "CO: PAYPAL" in u):
        return True
    if looks_like_transfer(name=desc, category=""):
        return True
    if re.search(r"\bNSF\b", u):
        return False  # fee = spend
    return False


def _merchant_from_desc(desc: str) -> str:
    d = re.sub(r"\s+", " ", desc).strip()
    # Prefer REF# merchant tail
    m = re.search(r"REF#:\s*\S+\s+\d{4}\s*-\s*(.+)$", d, re.I)
    if m:
        return m.group(1).strip()[:80]
    for pat in (
        r"MasterMoney Card\s*-\s*(.+)$",
        r"Debit Card\s*-\s*(.+)$",
        r"POS\s*#\d+\s*-\s*(.+)$",
        r"ATM\s*#\d+\s*-\s*(.+)$",
        r"CO:\s*(\S+)",
        r"Withdrawal\s+(.+)$",
        r"Deposit\s+(.+)$",
        r"Recurring Withdrawal Debit Card MasterMoney Card\s*-\s*(.+)$",
    ):
        m = re.search(pat, d, re.I)
        if m:
            return m.group(1).strip()[:80]
    return d[:80]


def _make_id(date_s: str, amount_cents: int, desc: str) -> str:
    h = hashlib.sha1(f"{date_s}|{amount_cents}|{desc}".encode()).hexdigest()[:16]
    return f"norcal-{date_s.replace('-', '')}-{h}"


def parse_statement_text(text: str, *, institution: str = "1st-norcal") -> list[Transaction]:
    lines = text.splitlines()
    txns: list[Transaction] = []
    i = 0
    in_atm_glance = False
    while i < len(lines):
        line = lines[i]
        # Recap table is not the register — skip until the next account section.
        if _ATM_GLANCE.search(line):
            in_atm_glance = True
            i += 1
            continue
        if in_atm_glance:
            if _ACCOUNT_SECTION.match(line.strip()):
                in_atm_glance = False
            i += 1
            continue
        # strip form-feed / page headers noise mid-stream
        if "Account Statement" in line and "Page:" in line:
            i += 1
            continue
        if _RECAP_LINE.match(line):
            i += 1
            continue
        m = _LINE_RE.match(line)
        if not m:
            i += 1
            continue
        date_raw, desc, amt_raw = m.group(1), m.group(2).strip(), m.group(3)
        if _SKIP_START.search(desc):
            i += 1
            continue
        # pull continuation lines into desc
        j = i + 1
        while j < len(lines):
            cont = lines[j]
            if _LINE_RE.match(cont) or not cont.strip():
                break
            stripped = cont.strip()
            # Statement often puts authorized date + REF#/MCC on the next line:
            #   07/25/2026 REF#: 6206DJGMJ7RY 5734 - GROK XAI ...
            # Do NOT treat that as a new txn start.
            auth_ref = bool(
                re.match(r"^\d{2}/\d{2}/\d{4}\b", stripped)
                and re.search(r"REF#:", stripped, re.I)
            )
            if re.match(r"^\s*\d{2}/\d{2}/\d{4}\b", cont) and not auth_ref:
                break
            if _CONT_RE.match(cont) or (
                cont.strip()
                and not cont.strip().startswith("Account Statement")
                and "Page:" not in cont
                and not re.match(r"^\s*PO Box", cont)
            ) or auth_ref:
                # avoid sucking in next section headers
                if re.match(r"^\s*(SAVINGS|CHECKING|MONEY MARKET|ATM ACTIVITY)", cont):
                    break
                desc = desc + " " + cont.strip()
                j += 1
            else:
                break
        i = j

        date_s = _mdy_to_iso(date_raw)
        amount_cents = _parse_amount(amt_raw)
        desc_clean = re.sub(r"\s+", " ", desc).strip()
        if "Starting Balance" in desc_clean or "Ending Balance" in desc_clean:
            continue
        if not _REAL_TXN_DESC.search(desc_clean):
            continue
        transfer = _is_transfer_desc(desc_clean)
        # Inflows (negative hermes amount) never count as spend; mark transfer for PayPal
        if amount_cents < 0 and "PAYPAL" in desc_clean.upper():
            transfer = True
        merchant = _merchant_from_desc(desc_clean)
        # crude category
        cat = "Misc / Other"
        u = desc_clean.upper()
        if "CSAA" in u or "INSURANCE" in u:
            cat = "Insurance"
        elif "COSTCO GAS" in u or "GAS #" in u:
            cat = "Gas"
        elif "COSTCO" in u:
            cat = "Groceries / Food"
        elif "GROK" in u or "XAI" in u or "HETZNER" in u or "ALOHI" in u:
            cat = "Software & Tools"
        elif "TMOBILE" in u or "T-MOBILE" in u or "US MOBILE" in u or "TWILIO" in u:
            cat = "Utilities & Phone"
        elif "CLIPPER" in u:
            cat = "Transportation"
        elif "PAYPAL" in u or "VENMO" in u:
            cat = "Transfer"
            transfer = True
        elif "NSF" in u:
            cat = "Irregular Expenses"
        elif "ATM" in u:
            cat = "Misc / Other"  # cash — still spend

        txns.append(
            Transaction(
                id=_make_id(date_s, amount_cents, desc_clean),
                date=date_s,
                amount_cents=amount_cents,
                name=desc_clean[:200],
                merchant_name=merchant[:80],
                category=cat,
                institution=institution,
                account_id="checking",
                pending=False,
                transfer=transfer,
                excluded=False,
            )
        )
    return txns


def pdf_to_text(path: Path) -> str:
    proc = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {proc.stderr[:300]}")
    return proc.stdout


def import_pdf(path: Path | str, *, institution: str = "1st-norcal") -> list[Transaction]:
    path = Path(path)
    text = pdf_to_text(path)
    return parse_statement_text(text, institution=institution)
