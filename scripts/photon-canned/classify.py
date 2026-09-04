#!/usr/bin/env python3
"""Classify Photon texts into canned $0 intents. No LLM.

Return None → fall through to Hermes (park / judgment).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_WS = re.compile(r"\s+")
_LEAD_CHAT = re.compile(
    r"^(?:hey|hi|yo|ok|okay|um|uh)[,\s]+",
    re.I,
)
_LEAD_PLEASE = re.compile(
    r"^(?:please|pls|plz|pretty please)\s+",
    re.I,
)
_LEAD_ASK = re.compile(
    r"^(?:can you|could you|would you|will you|can u)\s+",
    re.I,
)
_TRAIL_PUNCT = re.compile(r"[.!?…]+$")

# Implementation / copy-edit asks — do not steal these from Hermes.
_WORK = re.compile(
    r"\b("
    r"change|edit|fix|rewrite|implement|ship|patch|"
    r"wording|template|"
    r"pushover|notification style"
    r")\b",
    re.I,
)
_WORK_UPDATE = re.compile(
    r"\bupdate\b.+\b(copy|wording|template|notification|pushover|style)\b",
    re.I,
)

# "add X to the list" family — wins even if X contains "change".
_TODO_ADD = (
    re.compile(
        r"^(?:todo|to-do|to do)\s+add[:\s]+(.+)$",
        re.I,
    ),
    re.compile(
        r"^add\s+(?:a |an )?(?:todo|to-do|to do)(?: item)?[:\s]+(.+)$",
        re.I,
    ),
    re.compile(
        r"^add\s+(.+?)\s+to\s+(?:the\s+)?(?:todo|to-do|to do|standing)(?:\s+list)?$",
        re.I,
    ),
    re.compile(
        r"^tack\s+(.+?)\s+(?:on |onto )?(?:the\s+)?(?:todo |to-do |to do |standing )?list$",
        re.I,
    ),
    re.compile(
        r"^put\s+(.+?)\s+on\s+(?:the\s+)?(?:todo|to-do|to do|standing)(?:\s+list)?$",
        re.I,
    ),
    re.compile(r"^todo[:\s]+(.+)$", re.I),
)

_TODO_LIST = re.compile(
    r"^(?:"
    r"(?:todo|to-do|to do|standing)(?:\s+list)?"
    r"|list(?:\s+(?:the\s+)?(?:todos|to-dos|to dos|standing todos|open todos))?"
    r"|open todos"
    r"|what(?:'s| is|s) on the (?:todo |to-do |to do |standing )?list"
    r"|show(?: me)?(?: the)?(?: open)? todos"
    r")$",
    re.I,
)

_BUDGET = re.compile(
    r"^(?:"
    r"budget(?:\s+.*)?"
    r"|sts"
    r"|what(?:'s| is|s) (?:my )?(?:budget|safe ?-?to ?-?spend|sts)\b.*"
    r"|how(?:'s| is|s) (?:my )?(?:budget|spend|spending|pace)\b.*"
    r"|how am i doing(?: with | on )?(?:the )?(?:budget|spend|spending|pace|hardcap|sts)\b.*"
    r"|.*\bsafe ?-?to ?-?spend\b.*"
    r"|.*\bhardcap\b.*\b(left|remaining|status)\b.*"
    r"|.*\b(left|remaining)\b.*\b(hardcap|budget)\b.*"
    r"|am i (?:over|under|on) pace\b.*"
    r"|spend(?:ing)? (?:look|status|pace)\b.*"
    r")$",
    re.I,
)


@dataclass(frozen=True)
class Intent:
    kind: str  # budget | todo_add | todo_list
    title: Optional[str] = None


def _norm(text: str) -> str:
    t = (text or "").strip()
    t = t.lstrip("/")
    t = _WS.sub(" ", t)
    # peel polite wrappers a few times
    for _ in range(3):
        nxt = _LEAD_CHAT.sub("", t)
        nxt = _LEAD_PLEASE.sub("", nxt)
        nxt = _LEAD_ASK.sub("", nxt)
        nxt = _TRAIL_PUNCT.sub("", nxt).strip()
        if nxt == t:
            break
        t = nxt
    return t


def _clean_title(raw: str) -> str:
    t = _TRAIL_PUNCT.sub("", (raw or "").strip())
    t = t.strip(" \"'")
    return t


def _is_work(text: str) -> bool:
    return bool(_WORK.search(text) or _WORK_UPDATE.search(text))


def classify(text: str) -> Optional[Intent]:
    n = _norm(text)
    if not n or len(n) > 240:
        return None

    if _TODO_LIST.match(n):
        return Intent("todo_list")

    for pat in _TODO_ADD:
        m = pat.match(n)
        if m:
            title = _clean_title(m.group(1))
            if title and title.lower() not in {"list", "item", "items"}:
                return Intent("todo_add", title)

    if _is_work(n):
        return None

    if _BUDGET.match(n):
        return Intent("budget")

    return None
