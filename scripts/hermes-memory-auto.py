#!/usr/bin/env python3
"""Auto-patch live Hermes files before gateway startup.

1. Memory: Let Hermes's unattended review patch and remove its own memory.
   Hermes stages those ops and texts the staging line (#105921). There is
   no config switch. This rewrites the live install's memory_tool.py so
   the gate returns immediately.

2. Photon Watchdog: Ensure the zombie-stream probe passes `spc-msg-` prefix
   to space.getMessage() so Spectrum accepts the synthetic message GUID
   instead of rejecting with 'Expected message resource GUID'.

`hermes update` replaces upstream files, so the gateway systemd unit
runs this ExecStartPre before every start to re-apply any missing patches.
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = Path("/root/.hermes/HERMES_MEMORY_AUTO_CONSOLIDATE")
TARGET_MEM = Path("/usr/local/lib/hermes-agent/tools/memory_tool.py")
NEEDLE_MEM = "    from tools.skill_provenance import is_unattended_review\n"
INSERT_MEM = (
    "    # astra: unattended review may patch/remove when the marker exists.\n"
    "    # hermes-memory-auto.py re-inserts this after `hermes update`.\n"
    "    try:\n"
    "        from pathlib import Path as _AstraPath\n"
    "        if _AstraPath('/root/.hermes/HERMES_MEMORY_AUTO_CONSOLIDATE').is_file():\n"
    "            return None\n"
    "    except OSError:\n"
    "        pass\n"
)
STAMP_MEM = "astra: unattended review may patch/remove"

TARGET_PHOTON = Path("/usr/local/lib/hermes-agent/plugins/platforms/photon/sidecar/index.mjs")
STAMP_PHOTON = 'probeId.startsWith("spc-msg-")'
NEEDLE_PHOTON = "      await space.getMessage(probeId);\n"
INSERT_PHOTON = (
    '      const messageGuid = probeId.startsWith("spc-msg-")\n'
    '        ? probeId\n'
    '        : `spc-msg-${probeId}`;\n'
    '      await space.getMessage(messageGuid);\n'
)


def patch_memory() -> int:
    if not MARKER.is_file():
        print("hermes-memory-auto: marker absent, stock gate stays")
        return 0
    if not TARGET_MEM.is_file():
        print(f"hermes-memory-auto: missing {TARGET_MEM}", file=sys.stderr)
        return 1
    text = TARGET_MEM.read_text()
    if STAMP_MEM in text:
        print("hermes-memory-auto: memory patch already applied")
        return 0
    if NEEDLE_MEM not in text:
        print("hermes-memory-auto: gate shape changed; stock gate stays", file=sys.stderr)
        return 1
    TARGET_MEM.write_text(text.replace(NEEDLE_MEM, INSERT_MEM + NEEDLE_MEM, 1))
    print("hermes-memory-auto: memory patch applied")
    return 0


def patch_photon() -> int:
    if not TARGET_PHOTON.is_file():
        return 0
    text = TARGET_PHOTON.read_text()
    if STAMP_PHOTON in text:
        print("hermes-memory-auto: photon probe patch already applied")
        return 0
    if NEEDLE_PHOTON not in text:
        print("hermes-memory-auto: photon index.mjs probe structure changed; skipping", file=sys.stderr)
        return 0
    TARGET_PHOTON.write_text(text.replace(NEEDLE_PHOTON, INSERT_PHOTON, 1))
    print("hermes-memory-auto: photon probe patch applied")
    return 0


def main() -> int:
    rc_mem = patch_memory()
    rc_photon = patch_photon()
    return rc_mem or rc_photon


if __name__ == "__main__":
    raise SystemExit(main())
