"""Photon canned path — intercept budget/todo before the Hermes brain."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from actions import handle  # noqa: E402
from classify import classify  # noqa: E402

logger = logging.getLogger("plugins.photon-canned")

# Second line of the same switch. hermes-memory-auto.py rewrites the
# install before gateway start. This wrapper covers a process that
# imported the stock gate, and a deleted marker restores stock on the
# next call without another restart.
_AUTO_MARKER = "HERMES_MEMORY_AUTO_CONSOLIDATE"


def _auto_consolidate_enabled() -> bool:
    try:
        home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
        return (Path(home) / _AUTO_MARKER).is_file()
    except OSError:
        return False


_stock_background_delete_gate = None


def _open_gate(store, action, operations, target="memory", content=None, old_text=None):
    """Review patch/remove passes when the marker is present."""
    if _auto_consolidate_enabled():
        return None
    stock = _stock_background_delete_gate
    if stock is None:
        return None
    return stock(store, action, operations, target, content, old_text)


def _install_memory_auto_consolidate() -> None:
    """Let the unattended review apply patch/remove. Add already does.

    Idempotent. A missing marker, or a Hermes that dropped the gate,
    leaves the stock behavior alone. The wrapper still consults the
    marker on each call, so deleting the file restores the stock gate
    without another restart.
    """
    global _stock_background_delete_gate
    try:
        import tools.memory_tool as memory_tool
    except Exception:
        logger.debug("memory auto-consolidate: memory tool unavailable", exc_info=True)
        return
    current = memory_tool._background_delete_gate
    if getattr(current, "_astra_auto", False):
        return
    _stock_background_delete_gate = current
    _open_gate._astra_auto = True  # type: ignore[attr-defined]
    memory_tool._background_delete_gate = _open_gate


_install_memory_auto_consolidate()


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
        reply = handle(intent, author="Zavdi via Photon")
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
    from api_canned import wire_api_canned
    ctx.register_platform_handler("api_server", wire_api_canned)
