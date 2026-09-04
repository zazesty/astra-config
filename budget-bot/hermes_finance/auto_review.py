"""Automated txn review priors from offline history + keyword rules.

Not neural training — merchant/keyword → category map with confidence.
High-confidence (≥0.9) auto-accept; rest stays needs_review.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import state_dir
from .import_xlsx import extract_mcc, guess_merchant
from .models import Transaction
from .store import load_txns, save_txns

# Fin-coach category set (canonical labels)
CATEGORIES = [
    "Groceries / Food",
    "Misc / Other",
    "Shopping",
    "Insurance",
    "Medical & Health",
    "Personal Care",
    "Entertainment & Subs",
    "Charity / Donations",
    "Utilities & Phone",
    "Postage & Shipping",
    "Gas",
    "Dining Out",
    "Software & Tools",
    "Transportation",
    "Health & Fitness",
    "Housing",
    "Irregular Expenses",
    "Income",
    "Transfer",
]

# Plaid coarse labels → fin-coach
PLAID_CAT_MAP: dict[str, str] = {
    "SHOPS": "Shopping",
    "SHOPPING": "Shopping",
    "SERVICE": "Misc / Other",
    "FOOD AND DRINK": "Dining Out",
    "FOOD_AND_DRINK": "Dining Out",
    "TRAVEL": "Transportation",
    "RECREATION": "Entertainment & Subs",
    "COMMUNITY": "Charity / Donations",
    "HEALTHCARE": "Medical & Health",
    "BANK FEES": "Irregular Expenses",
    "PAYMENT": "Transfer",
    "TRANSFER": "Transfer",
    "INTEREST": "Income",
}

# ISO MCC → (category, confidence). Used when REF# line embeds MCC.
MCC_MAP: dict[str, tuple[str, float]] = {
    "5411": ("Groceries / Food", 0.92),  # grocery stores
    "5412": ("Groceries / Food", 0.9),
    "5422": ("Groceries / Food", 0.88),  # freezers/meat
    "5441": ("Groceries / Food", 0.88),  # candy/nut/confectionery
    "5451": ("Groceries / Food", 0.88),  # dairy
    "5462": ("Dining Out", 0.9),  # bakeries
    "5499": ("Groceries / Food", 0.85),  # misc food stores
    "5541": ("Gas", 0.93),  # service stations
    "5542": ("Gas", 0.95),  # automated fuel dispensers
    "5812": ("Dining Out", 0.93),  # eating places
    "5813": ("Dining Out", 0.9),  # bars
    "5814": ("Dining Out", 0.93),  # fast food
    "5912": ("Medical & Health", 0.9),  # drug stores
    "5942": ("Shopping", 0.9),  # book stores
    "5943": ("Shopping", 0.9),  # stationery
    "5947": ("Shopping", 0.88),  # gift/novelty
    "5977": ("Personal Care", 0.88),  # cosmetic stores
    "5995": ("Shopping", 0.9),  # pet shops
    "5999": ("Shopping", 0.92),  # misc specialty retail
    "5200": ("Shopping", 0.88),  # home supply
    "5211": ("Shopping", 0.9),  # lumber/building
    "5251": ("Shopping", 0.88),  # hardware
    "5261": ("Shopping", 0.88),  # lawn/garden
    "5300": ("Shopping", 0.88),  # wholesale clubs
    "5310": ("Shopping", 0.9),  # discount stores
    "5311": ("Shopping", 0.9),  # department stores
    "5331": ("Shopping", 0.9),  # variety stores
    "5399": ("Shopping", 0.88),  # misc general merchandise
    "5732": ("Shopping", 0.9),  # electronics
    "5734": ("Software & Tools", 0.9),  # computer software stores
    "5811": ("Dining Out", 0.85),  # catering
    "4111": ("Transportation", 0.9),  # local transit
    "4121": ("Transportation", 0.92),  # taxicabs/rideshare
    "4131": ("Transportation", 0.9),  # bus lines
    "4784": ("Transportation", 0.9),  # tolls
    "7523": ("Transportation", 0.9),  # parking
    "4215": ("Postage & Shipping", 0.92),  # courier
    "4812": ("Utilities & Phone", 0.88),  # telecom equipment
    "4814": ("Utilities & Phone", 0.9),  # telecom services
    "4816": ("Shopping", 0.88),  # computer network (Amazon often)
    "4899": ("Entertainment & Subs", 0.88),  # cable/pay TV
    "4900": ("Utilities & Phone", 0.9),  # utilities
    "6300": ("Insurance", 0.93),
    "6381": ("Insurance", 0.93),
    "8011": ("Medical & Health", 0.9),  # doctors
    "8021": ("Medical & Health", 0.9),  # dentists
    "8042": ("Medical & Health", 0.9),  # optometrists
    "8062": ("Medical & Health", 0.9),  # hospitals
    "8099": ("Medical & Health", 0.88),
    "8211": ("Irregular Expenses", 0.88),  # elementary/secondary schools
    "8220": ("Irregular Expenses", 0.9),  # colleges/universities
    "8299": ("Irregular Expenses", 0.85),  # schools/educational NEC
    "8351": ("Irregular Expenses", 0.85),  # child care
    "8398": ("Charity / Donations", 0.95),  # charitable orgs
    "8651": ("Charity / Donations", 0.9),  # political orgs
    "8661": ("Charity / Donations", 0.88),  # religious
    "7011": ("Irregular Expenses", 0.85),  # lodging
    "7832": ("Entertainment & Subs", 0.9),  # motion picture theaters
    "7922": ("Entertainment & Subs", 0.88),  # theatrical producers
    "7996": ("Entertainment & Subs", 0.88),  # amusement parks
    "7997": ("Health & Fitness", 0.88),  # membership clubs
    "7999": ("Entertainment & Subs", 0.85),  # recreation NEC
    "7230": ("Personal Care", 0.9),  # barber/beauty
    "7298": ("Personal Care", 0.88),  # health/beauty spas
    "7538": ("Transportation", 0.88),  # auto service shops
    "7542": ("Transportation", 0.88),  # car washes
    "5532": ("Transportation", 0.88),  # automotive tire
    "5533": ("Transportation", 0.88),  # automotive parts
    "5511": ("Irregular Expenses", 0.85),  # car dealers
    "7523": ("Transportation", 0.9),
}

# (pattern, category, confidence) — applied to merchant+name uppercased
RULES: list[tuple[str, str, float]] = [
    # income / transfer-ish
    (r"\bBRIAN ROHSENOW\b", "Income", 0.95),
    (r"\bGENERAL PAYMENT\b.*ROHSENOW", "Income", 0.9),
    # County payroll posts as "Deposit CONTRA - CO: CONTRA". Bare CONTRA matches
    # Contra Costa street addresses (Safeway, St. VIN, Ace, …) and mislabels spend.
    (r"\bCO:\s*CONTRA\b", "Income", 0.93),
    (r"\bAEROTEK\b|\bPAYROLL\b|\bDIRECT DEP", "Income", 0.93),
    (r"\bDEPOSIT\b.*(HOME BANKING|NIGHT DROP|MOBILE)", "Income", 0.92),
    (r"\bACCOUNT HOLD\b|\bREVERSAL OF GENERAL ACCOUNT HOLD\b", "Transfer", 0.95),
    (r"\bGENERAL CURRENCY CONVERSION\b", "Transfer", 0.9),
    (r"\bMONEY TRANSFER (TO|FROM)\b", "Transfer", 0.95),
    (r"\bPARTNER FEE\b", "Misc / Other", 0.85),
    # eBay: outflow shopping, inflow refund-ish income
    (r"\bEBAY\b", "Shopping", 0.9),
    # insurance
    (r"\bCSAA\b|\bINSURANCE\b|\bVSP\b|\bNATLSTDNTSERV", "Insurance", 0.95),
    # phone / utilities
    (r"\bUS MOBILE\b|\bVERIZON\b|\bT-?MOBILE\b|\bAT&T\b", "Utilities & Phone", 0.95),
    (r"\bPOSTSCANMAIL\b", "Postage & Shipping", 0.95),
    # software / AI / subs
    (r"\bANTHROPIC\b|\bCLAUDE\.AI\b|\bOPENAI\b|\bX\.AI\b|\bSUPERGROK\b", "Software & Tools", 0.95),
    (r"\bMICROSOFT\b|\bGOOGLE\b|\bPROTON\b|\bBEONEX\b|\bSNAPWISE\b", "Software & Tools", 0.9),
    (r"\bOPENROUTER\b", "Software & Tools", 0.95),
    (r"\bBROTHER REFRESH\b|\bBROTHER\b.*PRI", "Shopping", 0.9),  # toner sub (ended)
    (r"\bGOFNDME\b|\bGOFUNDME\b", "Charity / Donations", 0.93),
    (r"\bDIVINE\s*DOVE\b", "Charity / Donations", 0.95),
    (r"\bAGLAE\b", "Charity / Donations", 0.95),
    (r"\bALOHI\b", "Software & Tools", 0.93),  # virtual fax SaaS
    (r"\bNSF\b|\bINSUFFICIENT\b|\bOVERDRAFT\b", "Irregular Expenses", 0.95),
    (r"\bDEBTOREDU\b|\bDEBTORCC\b|\bDEBTOR\s*EDU", "Irregular Expenses", 0.93),
    (r"\bCORPORATE FILINGS\b", "Irregular Expenses", 0.9),
    (r"\bSPOTIFY\b|\bNETFLIX\b|\bAPPLE SERVICES\b", "Entertainment & Subs", 0.92),
    # iCloud / Apple.com/bill = SaaS (not Apple Cash P2P)
    (r"\bAPPLE\.COM/BILL\b|\bICLOUD\b", "Software & Tools", 0.94),
    # plain APPLE (not Cash P2P — handled in rule_review; not Pay)
    (r"\bAPPLE\b(?!.*(?:CASH|PAY))", "Entertainment & Subs", 0.88),
    (r"\bGROK\b|\bXAI\b", "Software & Tools", 0.95),
    # dining / campus
    (r"\bCAFETERI|\bDVC CAFETERIA\b|\bLANEY COLLEGE CAFETER", "Dining Out", 0.93),
    (r"\bCTLP\*J AND J VENDING\b|\bJ AND J VENDING\b|\bVENDING\b", "Dining Out", 0.93),
    (r"\bTOO GOOD TO GO\b", "Dining Out", 0.95),
    (r"\bSHAKE SHACK\b", "Dining Out", 0.93),
    (r"\bGOOD TABLE\b", "Dining Out", 0.93),
    # campus laundry kiosks (not food)
    (r"\bCTLP\*CSC\b|\bCSC SERVICEWORKS\b|\bSERVICEWORKS\b", "Personal Care", 0.9),
    # personal care
    (r"\bFASHION CLEANERS\b|\bCLEANERS\b|\bDRY\s*CLEAN", "Personal Care", 0.92),
    # health / supplements
    (r"\bIHERB\b|\bPHARMACY\b|\bOPTOMETR|\bCVS\b|\bWALGREENS\b|\bMEDICAL\b|\bTHORNE\b", "Medical & Health", 0.9),
    (r"\bSEED\.COM\b|\bSEED\b.*HAMPTON", "Medical & Health", 0.93),
    (r"\bEYE LOVE\b", "Medical & Health", 0.95),
    # school / bookstore
    (r"\bDVC BOOKSTORE\b", "Shopping", 0.9),  # also PERALTA CC DISTRICT below
    # groceries
    (r"\bSAFEWAY(?!\s*FUEL)\b|\bALBERTSONS?\b|\bGROCERY\b|\bTRADER JOE|\bCOSTCO(?!\s*GAS)\b|\bGRAND MARKET\b|\bRALEY|\bVIKS MARKET\b", "Groceries / Food", 0.92),
    # gas (+ Monument 76 large fills handled in rule_review)
    (r"\bSAFEWAY FUEL|\bCHEVRON\b|\bSHELL\b|\bCOSTCO GAS\b|\bCENTRAL GAS\b|\bGAS\b|\bFUEL\b", "Gas", 0.93),
    (r"\bMONUMENT\s*76\b", "Gas", 0.85),  # amount split in rule_review
    # personal care
    (r"\bBARBER\b|\bVAGARO\b|\bSILVER FOX\b|\bHAIR\b|\bSALON\b", "Personal Care", 0.9),
    # shipping
    (r"\bUPS\b|\bUSPS\b|\bFEDEX|\bPOSTAGE\b|\bSHIPPING\b|\bROLLO\b", "Postage & Shipping", 0.93),
    # charity
    (r"\bACTBLUE\b|\bEFF\b|ELECTRONIC FRONTIER|\bDONATION\b|\bCHARITY\b", "Charity / Donations", 0.93),
    # transport
    (r"\bPARKING\b|\bUBER\b|\bLYFT\b|\bBART\b|\bTRANSIT\b|\bTOLL\b", "Transportation", 0.9),
    (r"\bAUTOZONE\b|\bO[\u2019']?REILLY\b", "Transportation", 0.9),
    (r"\bFASTRAK\b|\bFAS ?TRAK\b", "Transportation", 0.95),
    (r"\bGRAND PRIX EXPRESS\b|\bCAR\s*WASH", "Transportation", 0.93),  # car wash(es)
    (r"\bACE HARDWARE\b|\bMARKUS SUPPLY\b|\bAIRGAS\b", "Shopping", 0.92),
    (r"\bPERALTA CC DISTRICT\b|\bDVC BOOKSTORE\b", "Shopping", 0.92),
    (r"\bPUSHOVER\b", "Software & Tools", 0.93),
    (r"\bOPEN SOURCE COLLECTIVE\b|\bOPENCOLLECTIVE\b", "Charity / Donations", 0.9),
    (r"\bGERMANIKURE\b", "Personal Care", 0.92),
    (r"\bJ\s*&\s*A MARKET\b|\bJ AND A MARKET\b", "Misc / Other", 0.92),
    (r"\bMIHARU\b|\bICE CREAM\b", "Dining Out", 0.93),
    (r"\bBIG O TIRES?\b", "Transportation", 0.92),
    # shopping / gear
    (r"\bEBAY\b|\bAMAZON\b|\bSWAPPA\b|\bTARGET\b|\bWALMART\b|\bDOLLARTREE\b|\bDOLLAR TREE\b|\bSTAPLES\b|\bMICHAELS\b|\bLOWE[\u2019']?S\b|\bPETCO\b|\b3DCONNEXION\b|\bBIG DAVE[\u2019']?S? BIKES?\b|\bBLICK ART\b|\bETSY\b", "Shopping", 0.93),
    # dining
    (r"\bCAFE\b|\bRESTAURANT\b|\bDOORDASH\b|\bGRUBHUB\b|\bSTARBUCKS\b|\bMCDONALD|\b7-?ELEVEN\b|\bSALT AND STRAW\b|\bTACOS\b", "Dining Out", 0.9),
    # groceries
    (r"\bSPROUTS\b", "Groceries / Food", 0.93),
    (r"\bTOTAL WINE\b", "Shopping", 0.9),  # alcohol retail
    # gas / speedway-style
    (r"\bSPEEDWAY\b|\b68188\b", "Gas", 0.9),
    # school / admin → Irregular (no separate "College" category)
    (r"\bPARCHMENT\b|\bCOLLEGE TRANSCRIPT\b|\bTHE PERMIT STORE\b|\bCAL ST UNI\b|\bPARKING PERMIT\b|\bUNIVERSITY\b", "Irregular Expenses", 0.9),
    # software / hosting / subs
    (r"\bTASKADE\b|\bSLACK\b|\bHETZNER\b", "Software & Tools", 0.92),
    (r"\bPRIME VIDEO\b", "Entertainment & Subs", 0.93),
    (r"\bVALVE CORPORATION\b|\bVALVE\b|\bSTEAM\b", "Entertainment & Subs", 0.93),
    (r"\bX CORP\.?\s*PAID\b", "Entertainment & Subs", 0.93),
    # paypal partner fee
    (r"\bPARTNER FEE\b", "Misc / Other", 0.92),
    # thrift / small retail (St. Vincent de Paul = clothes, not a donation)
    (r"\bYOUTH HOMES THRIFT\b|\bUNITED BROTHERS\b", "Shopping", 0.9),
    (r"\bST\.?\s*VIN|\bSTVINCENT|\bSOCIETY OF ST\.?\s*VIN", "Shopping", 0.93),
    (r"\bOAKLAND MUSEUM\b|\bOMCA\b", "Entertainment & Subs", 0.9),
    # irregular
    (r"\bIRS\b|\bDISCOUNT TIRE\b|\bTAX\b|\bDMV\b", "Irregular Expenses", 0.9),
    # fitness
    (r"\bGYM\b|\bFITNESS\b|\bPLANET FITNESS\b", "Health & Fitness", 0.9),
    # venmo person-to-person handled in rule_review by amount
    (r"\bVENMO\b", "Misc / Other", 0.7),
]


# Venmo / P2P under this amount → auto Misc (owner policy 2026-07-26)
VENMO_AUTO_MISC_MAX_CENTS = 2000  # $20
# Monument 76: gas fill vs c-store snack
MONUMENT76_GAS_MIN_CENTS = 1500  # $15+


@dataclass
class ReviewResult:
    category: str
    confidence: float
    review_status: str  # auto_accepted | needs_review | excluded
    reason: str


def _blob(t: Transaction) -> str:
    return f"{t.merchant_name or ''} {t.name or ''}".upper()


def _status(conf: float, floor: float = 0.9) -> str:
    return "auto_accepted" if conf >= floor else "needs_review"


def _maybe_refresh_merchant(t: Transaction) -> None:
    """Fix opaque merchant_name (POS #… / REF# without merchant) from full name."""
    m = (t.merchant_name or "").strip()
    if m and not re.match(
        r"^(POS\s*#\d+|MASTERMONEY CARD REF#.*|MASTERMONEY CARD)$", m, re.I
    ):
        return
    guessed = guess_merchant(t.name or "")
    if guessed and guessed != m:
        t.merchant_name = guessed


def _mcc_review(t: Transaction) -> ReviewResult | None:
    mcc = extract_mcc(t.name or "")
    if not mcc or mcc not in MCC_MAP:
        return None
    cat, conf = MCC_MAP[mcc]
    return ReviewResult(cat, conf, _status(conf), f"mcc:{mcc}")


def _plaid_cat_review(t: Transaction) -> ReviewResult | None:
    """Map leftover Plaid coarse categories if still on the txn."""
    raw = (t.category or "").strip()
    key = raw.upper().replace("&", "AND")
    if key in PLAID_CAT_MAP:
        cat = PLAID_CAT_MAP[key]
        return ReviewResult(cat, 0.8, "needs_review", f"plaid_cat:{raw}")
    if raw in CATEGORIES:
        return None
    return None


def rule_review(t: Transaction) -> ReviewResult:
    from .dedupe import is_import_institution
    from .transfers import is_debit_card_purchase, looks_like_transfer

    # Plaid often tags MasterMoney as TRANSFER_OUT. Repair the transfer flag.
    # This also revives statement-SSOT twins; persist_statement_ssot must run
    # after auto-review so PDF/xlsx still wins on (date, amount).
    debit_purchase = is_debit_card_purchase(
        name=t.name or "", merchant_name=t.merchant_name
    )
    if debit_purchase and t.transfer:
        t.transfer = False
        if not is_import_institution(t.institution):
            t.excluded = False
    elif t.transfer and not looks_like_transfer(
        name=t.name or "", merchant_name=t.merchant_name, category=""
    ):
        # Stale ACH bill-pay tagged Transfer (CSAA). Statement twins stay excluded.
        t.transfer = False
        if is_import_institution(t.institution):
            t.excluded = False

    _maybe_refresh_merchant(t)
    blob = _blob(t)
    if t.transfer or t.excluded:
        return ReviewResult("Transfer", 0.99, "excluded", "flagged_transfer_or_excluded")

    # Venmo P2P: under $20 → Misc auto; else needs_review
    if re.search(r"\bVENMO\b", blob, re.I):
        if 0 < t.amount_cents <= VENMO_AUTO_MISC_MAX_CENTS:
            return ReviewResult(
                "Misc / Other", 0.92, "auto_accepted", "venmo_under_20_misc"
            )
        if t.amount_cents > VENMO_AUTO_MISC_MAX_CENTS:
            return ReviewResult(
                "Misc / Other", 0.5, "needs_review", "venmo_over_20_review"
            )

    # Apple Cash person-to-person: always ask (not Apple Services sub).
    # One known historical: 2026-06-07 $50 haircut → Personal Care.
    if re.search(r"\bAPPLE CASH\b", blob, re.I):
        if t.date == "2026-06-07" and t.amount_cents == 5000:
            return ReviewResult(
                "Personal Care", 0.95, "auto_accepted", "owner:apple_cash_haircut"
            )
        return ReviewResult(
            "Misc / Other", 0.35, "needs_review", "apple_cash_ask"
        )

    # Owner pins for opaque / adjustment lines (historical)
    if t.date == "2026-03-02" and t.amount_cents == 2523:
        return ReviewResult("Shopping", 0.95, "auto_accepted", "owner:amazon_opaque")
    if t.date == "2026-03-07" and t.amount_cents == -2194:
        return ReviewResult("Shopping", 0.95, "auto_accepted", "owner:autozone_refund")
    if t.date == "2026-05-16" and t.amount_cents == -1690:
        return ReviewResult("Shopping", 0.95, "auto_accepted", "owner:amazon_return")
    # ATM no-cash: $63.50 debit + matching adjustment cancel (fee attempt)
    if t.date == "2026-06-06" and abs(t.amount_cents) == 6350 and re.search(
        r"\bATM\b", blob, re.I
    ):
        return ReviewResult("Transfer", 0.99, "excluded", "owner:atm_no_cash_cancel")

    # Monument 76: gas if ≥$15, else c-store Misc
    if re.search(r"\bMONUMENT\s*76\b", blob, re.I):
        if t.amount_cents >= MONUMENT76_GAS_MIN_CENTS:
            return ReviewResult("Gas", 0.92, "auto_accepted", "monument76_gas")
        return ReviewResult("Misc / Other", 0.9, "auto_accepted", "monument76_cstore")

    # Apply keyword rules to both inflows and outflows; then specialize inflows
    best: ReviewResult | None = None
    for pat, cat, conf in RULES:
        if re.search(pat, blob, re.I):
            if best is None or conf > best.confidence:
                best = ReviewResult(
                    cat,
                    conf,
                    _status(conf),
                    f"rule:{pat[:40]}",
                )

    mcc_hit = _mcc_review(t)
    if mcc_hit and (best is None or mcc_hit.confidence > best.confidence):
        # prefer stronger MCC when keyword was weak/generic
        best = mcc_hit
    elif mcc_hit and best and best.confidence < 0.9 and mcc_hit.confidence >= 0.9:
        best = mcc_hit

    if t.amount_cents < 0:
        # Refunds net spend — keep a spend category, never label them Income.
        if t.looks_like_refund() or re.search(
            r"credit\s*voucher|adjustment.*credit|\brefund", blob, re.I
        ):
            cat = "Misc / Other"
            if best and best.category not in ("Income", "Transfer", ""):
                cat = best.category
            return ReviewResult(cat, 0.93, "auto_accepted", "inflow_refund")
        # money in: prefer Income unless rule said Transfer/Shopping refund etc.
        if best and best.category in ("Transfer", "Shopping", "Income") and best.confidence >= 0.9:
            if best.category == "Shopping":
                return ReviewResult("Shopping", 0.9, "auto_accepted", "inflow_refundish_shopping")
            return best
        if best and best.confidence >= 0.9 and best.category == "Income":
            return best
        if re.search(r"ROHSENOW|PAYROLL|DIRECT DEP|CO:\s*CONTRA|AEROTEK|DEPOSIT|PAYMENT FROM|EXPRESS CHECKOUT", blob):
            return ReviewResult("Income", 0.92, "auto_accepted", "inflow_incomeish")
        # person Express Checkout (e.g. Swappa buyer) already covered; keep high conf Income
        if re.search(r"MONEY TRANSFER FROM|TRANSFER FROM", blob):
            return ReviewResult("Transfer", 0.95, "excluded", "inflow_transfer")
        return ReviewResult("Income", 0.7, "needs_review", "inflow_unknown")

    if best:
        if best.confidence < 0.9:
            best.review_status = "needs_review"
        return best

    # Plaid leftover label as weak prior
    plaid = _plaid_cat_review(t)
    if plaid:
        return plaid

    # weak POS / ATM / opaque with no MCC/merchant rule
    if re.search(r"\bPOS\s*#|\bATM\b|MASTERMONEY CARD REF", blob):
        return ReviewResult("Misc / Other", 0.4, "needs_review", "opaque_pos_atm")

    return ReviewResult("Misc / Other", 0.35, "needs_review", "no_rule")


def apply_review(
    txns: list[Transaction],
    auto_threshold: float = 0.9,
) -> tuple[list[Transaction], dict[str, Any]]:
    """Mutate categories; store review fields on dict export via side channel.

    Transaction dataclass has no review fields — we return stats + write
    parallel review.json keyed by id.
    """
    reviews: dict[str, dict[str, Any]] = {}
    stats = defaultdict(int)
    cat_counts: dict[str, int] = defaultdict(int)

    for t in txns:
        r = rule_review(t)
        # Never force-exclude CU debit-card / MasterMoney purchases as transfers
        # (Plaid often labels them TRANSFER_OUT; false excludes hid AI spend).
        from .dedupe import is_import_institution
        from .transfers import is_debit_card_purchase

        debit_purchase = is_debit_card_purchase(
            name=t.name or "", merchant_name=t.merchant_name
        )
        if t.excluded and r.review_status == "excluded":
            status = "excluded"
            t.category = r.category
        elif (
            not debit_purchase
            and (
                r.review_status == "excluded"
                or (r.category == "Transfer" and r.confidence >= 0.9)
            )
        ):
            status = "excluded"
            t.excluded = True
            t.transfer = True
            t.category = "Transfer"
        elif r.confidence >= auto_threshold and r.review_status == "auto_accepted":
            status = "auto_accepted"
            t.category = r.category
            # repair prior false transfer tags on debit purchases (not import twins)
            if debit_purchase and t.transfer and r.category != "Transfer":
                t.transfer = False
                if not is_import_institution(t.institution):
                    t.excluded = False
        else:
            status = "needs_review"
            t.category = r.category  # proposed label always
            if debit_purchase and t.transfer and r.category != "Transfer":
                t.transfer = False
                if not is_import_institution(t.institution):
                    t.excluded = False
                    if r.confidence >= auto_threshold:
                        status = "auto_accepted"

        reviews[t.id] = {
            "category": r.category,
            "confidence": r.confidence,
            "review_status": status,
            "reason": r.reason,
            "merchant": t.display_name(),
            "amount_cents": t.amount_cents,
            "date": t.date,
        }
        stats[status] += 1
        cat_counts[r.category] += 1

    # merchant prior map for future txns
    merchant_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        if t.amount_cents <= 0:
            continue
        m = t.display_name().upper()
        merchant_cat[m][t.category] += 1
    priors = {
        m: max(cats.items(), key=lambda x: x[1])[0]
        for m, cats in merchant_cat.items()
        if sum(cats.values()) >= 2
    }

    summary = {
        "n": len(txns),
        "status_counts": dict(stats),
        "category_counts": dict(sorted(cat_counts.items(), key=lambda x: -x[1])),
        "auto_accept_rate": round(stats["auto_accepted"] / max(len(txns), 1), 4),
        "needs_review_rate": round(stats["needs_review"] / max(len(txns), 1), 4),
        "merchant_priors": len(priors),
        "auto_threshold": auto_threshold,
    }
    return txns, {"summary": summary, "reviews": reviews, "merchant_priors": priors}


def _write_review_queues(txns: list[Transaction], reviews: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Rebuild opaque POS / Venmo queues for human pass."""
    qdir = state_dir() / "review_queues"
    qdir.mkdir(parents=True, exist_ok=True)
    opaque: list[dict[str, Any]] = []
    venmo: list[dict[str, Any]] = []
    for t in txns:
        rev = reviews.get(t.id) or {}
        if rev.get("review_status") != "needs_review":
            continue
        blob = f"{t.merchant_name or ''} {t.name or ''}".upper()
        d = t.to_dict()
        if re.search(r"\bVENMO\b", blob):
            venmo.append(d)
        elif re.search(r"\bPOS\s*#|MASTERMONEY CARD REF", blob) and rev.get("confidence", 1) < 0.9:
            opaque.append(d)
    (qdir / "opaque_pos_ref.json").write_text(
        json.dumps(opaque, indent=2, sort_keys=True) + "\n"
    )
    (qdir / "venmo.json").write_text(json.dumps(venmo, indent=2, sort_keys=True) + "\n")
    try:
        (qdir / "opaque_pos_ref.json").chmod(0o600)
        (qdir / "venmo.json").chmod(0o600)
    except OSError:
        pass
    return {"opaque_pos_ref": len(opaque), "venmo": len(venmo)}


def run_and_persist(auto_threshold: float = 0.9) -> dict[str, Any]:
    txns = load_txns()
    txns, payload = apply_review(txns, auto_threshold=auto_threshold)
    save_txns(txns)
    queues = _write_review_queues(txns, payload["reviews"])
    payload["summary"]["review_queues"] = queues
    out = state_dir() / "auto_review.json"
    priors_path = state_dir() / "merchant_category_priors.json"
    tmp = {
        "summary": payload["summary"],
        "reviews": payload["reviews"],
    }
    out.write_text(json.dumps(tmp, indent=2, sort_keys=True) + "\n")
    priors_path.write_text(json.dumps(payload["merchant_priors"], indent=2, sort_keys=True) + "\n")
    try:
        out.chmod(0o600)
        priors_path.chmod(0o600)
    except OSError:
        pass
    return payload["summary"]


def review_one(t: Transaction, priors: dict[str, str] | None = None) -> ReviewResult:
    """Online path: prior merchant map then rules."""
    if priors:
        m = t.display_name().upper()
        if m in priors:
            return ReviewResult(priors[m], 0.92, "auto_accepted", "merchant_prior")
    return rule_review(t)
