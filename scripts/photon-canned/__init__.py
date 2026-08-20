"""Photon canned path — intercept budget/todo before grok-4.6."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from actions import handle  # noqa: E402
from classify import classify  # noqa: E402

logger = logging.getLogger("plugins.photon-canned")


def _platform_name(source) -> str:
    plat = getattr(source, "platform", None)
    return str(getattr(plat, "value", plat) or "").lower()


async def _send(adapter, chat_id: str, text: str) -> None:
    try:
        await adapter.send(chat_id, text)
    except Exception:
        logger.exception("photon-canned send failed")


def pre_gateway_dispatch(event, gateway, **kwargs):
    del kwargs
    src = getattr(event, "source", None)
    if src is None or _platform_name(src) != "photon":
        return None
    text = getattr(event, "text", None) or ""
    intent = classify(text)
    if intent is None:
        return None

    try:
        reply = handle(intent)
    except Exception:
        logger.exception("photon-canned handle failed")
        reply = "Canned path failed. Try again or ask Grok Build."

    adapters = getattr(gateway, "adapters", None) or {}
    plat = getattr(src, "platform", None)
    adapter = None
    try:
        adapter = adapters.get(plat)
    except TypeError:
        adapter = None
    if adapter is None:
        want = _platform_name(src)
        for key, val in adapters.items():
            if str(getattr(key, "value", key)).lower() == want:
                adapter = val
                break
    chat_id = getattr(src, "chat_id", None)
    if adapter is not None and chat_id:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send(adapter, chat_id, reply))
        except RuntimeError:
            logger.warning("photon-canned: no event loop; skip send")

    return {"action": "skip", "reason": f"canned-{intent.kind}"}


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
