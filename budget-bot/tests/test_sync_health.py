#!/usr/bin/env python3
"""Unit tests for Plaid sync-break detection (no network)."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_finance.sync_health import (
    ITEM_BREAK_CODES,
    break_email_body,
    break_kind,
    break_notify_key,
    break_push_body,
    break_subject,
    classify_item_health,
    collect_failures,
    friendly_institution,
    parse_plaid_ts,
    process_sync_health,
    repair_link_expired,
)

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 8, 16, 3, 30, tzinfo=UTC)


class TestClassifyItemHealth(unittest.TestCase):
    def test_login_required_is_break(self):
        row = classify_item_health(
            item_id="abc",
            institution="1st-northern-california-credit-union",
            item_error={
                "error_code": "ITEM_LOGIN_REQUIRED",
                "error_message": "the login details of this item have changed",
            },
            last_successful_update="2026-08-11T22:03:32Z",
            last_failed_update="2026-08-16T03:12:04Z",
            now=NOW,
        )
        self.assertIsNotNone(row)
        self.assertIn("ITEM_LOGIN_REQUIRED", row["error"])

    def test_healthy_recent_success(self):
        row = classify_item_health(
            item_id="abc",
            institution="paypal",
            item_error=None,
            last_successful_update="2026-08-15T22:08:21Z",
            last_failed_update="2026-07-27T06:04:56Z",
            now=NOW,
            stale_hours=72,
        )
        self.assertIsNone(row)

    def test_stale_after_failed_update(self):
        row = classify_item_health(
            item_id="abc",
            institution="1st-northern-california-credit-union",
            item_error=None,
            last_successful_update="2026-08-11T22:03:32Z",
            last_failed_update="2026-08-15T10:09:44Z",
            now=NOW,
            stale_hours=72,
        )
        self.assertIsNotNone(row)
        self.assertIn("stale_transactions", row["error"])
        self.assertIn("last_fail=", row["error"])

    def test_recent_fail_after_relogin_not_stale(self):
        row = classify_item_health(
            item_id="abc",
            institution="1st-northern-california-credit-union",
            item_error=None,
            last_successful_update="2026-08-11T22:03:32Z",
            last_failed_update=NOW.isoformat(),
            now=NOW,
            stale_hours=72,
            stale_grace_hours=6,
        )
        self.assertIsNone(row)

    def test_fresh_fail_within_window_ok(self):
        # success 12h ago, fail 1h ago — wait, don't email yet
        row = classify_item_health(
            item_id="abc",
            institution="paypal",
            item_error=None,
            last_successful_update=(NOW - timedelta(hours=12)).isoformat(),
            last_failed_update=(NOW - timedelta(hours=1)).isoformat(),
            now=NOW,
            stale_hours=72,
        )
        self.assertIsNone(row)

    def test_parse_zulu(self):
        dt = parse_plaid_ts("2026-08-11T22:03:32.38Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, UTC)


class TestCollectAndMerge(unittest.TestCase):
    def test_collect_failed_items(self):
        rows = collect_failures(
            {
                "failed_items": [
                    {
                        "item_id": "abc",
                        "institution": "norcal",
                        "error": "ITEM_LOGIN_REQUIRED",
                    }
                ]
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["error"], "ITEM_LOGIN_REQUIRED")

    def test_merge_only_does_not_clear_other_item(self):
        import tempfile
        from unittest.mock import patch

        from hermes_finance import sync_health

        with tempfile.TemporaryDirectory() as td:
            health_path = Path(td) / "sync_health.json"
            health_path.write_text(
                '{"failures": {"keep-me": {"institution": "paypal", '
                '"error": "old", "first_seen": "2026-08-01T00:00:00+00:00", '
                '"email_sent_at": "2026-08-01T00:00:00+00:00", '
                '"pushover_sent_at": null}}}\n'
            )

            def _path():
                return health_path

            with (
                patch.object(sync_health, "_path", _path),
                patch.object(sync_health, "send_alert", return_value="dry_run"),
                patch.object(
                    sync_health,
                    "load_config",
                    return_value={
                        "sync_break_email": True,
                        "sync_break_pushover_after_days": 3,
                    },
                ),
            ):
                process_sync_health(
                    {
                        "failed_items": [
                            {
                                "item_id": "new-one",
                                "institution": "norcal",
                                "error": "ITEM_LOGIN_REQUIRED",
                            }
                        ]
                    },
                    dry_run=True,
                    merge_only=["new-one"],
                )
                data = sync_health.load_health()
                self.assertIn("keep-me", data["failures"])
                self.assertIn("new-one", data["failures"])

    def test_break_codes_include_login(self):
        self.assertIn("ITEM_LOGIN_REQUIRED", ITEM_BREAK_CODES)

    def test_same_day_rebreak_gets_new_push_key(self):
        import tempfile
        from unittest.mock import patch

        from hermes_finance import sync_health

        calls: list[str] = []
        clock = {"t": datetime(2026, 8, 25, 6, 55, 14, tzinfo=UTC)}

        def _send(ev, **_kw):
            calls.append(ev.key)
            return "sent_push"

        def _now():
            return clock["t"]

        norcal = {
            "item_id": "nymR-test",
            "institution": "1st-northern-california-credit-union",
            "error": "ITEM_LOGIN_REQUIRED",
        }
        cfg = {
            "sync_break_email": False,
            "sync_break_pushover_after_days": 0,
        }

        with tempfile.TemporaryDirectory() as td:
            health_path = Path(td) / "sync_health.json"
            health_path.write_text('{"failures": {}}\n')

            with (
                patch.object(sync_health, "_path", lambda: health_path),
                patch.object(
                    sync_health,
                    "_health_log_path",
                    lambda: Path(td) / "sync_health.log",
                ),
                patch.object(sync_health, "_now", _now),
                patch.object(sync_health, "send_alert", side_effect=_send),
                patch.object(sync_health, "load_config", return_value=cfg),
                patch(
                    "hermes_finance.plaid_sync.item_repair_grace_active",
                    return_value=False,
                ),
                patch(
                    "hermes_finance.plaid_link_server.mint_repair_link",
                    return_value={"public_url": "https://example.invalid/r"},
                ),
            ):
                process_sync_health(
                    {"failed_items": [norcal]}, dry_run=False
                )
                process_sync_health(
                    {"failed_items": [], "items": []}, dry_run=False
                )
                clock["t"] = datetime(2026, 8, 25, 16, 10, 23, tzinfo=UTC)
                process_sync_health(
                    {"failed_items": [norcal]}, dry_run=False
                )

        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertIn("2026-08-25T06:55:14", calls[0])
        self.assertIn("2026-08-25T16:10:23", calls[1])


class TestNotifyKey(unittest.TestCase):
    def test_episode_key_includes_time(self):
        a = datetime(2026, 8, 25, 6, 55, 14, tzinfo=UTC)
        b = datetime(2026, 8, 25, 16, 10, 23, tzinfo=UTC)
        k1 = break_notify_key("push", "nymR", a)
        k2 = break_notify_key("push", "nymR", b)
        self.assertNotEqual(k1, k2)
        self.assertIn("2026-08-25T06:55:14", k1)
        self.assertIn("2026-08-25T16:10:23", k2)

    def test_relink_wave_changes_key(self):
        first = datetime(2026, 8, 30, 12, 26, 52, tzinfo=UTC)
        wave = datetime(2026, 9, 3, 6, 40, tzinfo=UTC)
        k1 = break_notify_key("push", "nymR", first)
        k2 = break_notify_key("push", "nymR", first, wave=wave)
        self.assertNotEqual(k1, k2)
        self.assertIn("relink", k2)


class TestRepairLinkExpired(unittest.TestCase):
    def test_missing_expiry_after_push_is_dead(self):
        now = datetime(2026, 9, 3, 6, 40, tzinfo=UTC)
        self.assertTrue(
            repair_link_expired(
                {"pushover_sent_at": "2026-08-30T12:26:52+00:00"}, now
            )
        )
        self.assertFalse(repair_link_expired({}, now))
        # 24h backoff if we pushed without storing expiry
        self.assertFalse(
            repair_link_expired(
                {"pushover_sent_at": "2026-09-03T06:00:00+00:00"}, now
            )
        )

    def test_future_expiry_is_live(self):
        now = datetime(2026, 9, 3, 6, 40, tzinfo=UTC)
        self.assertFalse(
            repair_link_expired(
                {
                    "pushover_sent_at": now.isoformat(),
                    "repair_expires_at": "2026-09-04T06:40:00Z",
                },
                now,
            )
        )
        self.assertTrue(
            repair_link_expired(
                {
                    "pushover_sent_at": "2026-08-30T12:26:52+00:00",
                    "repair_expires_at": "2026-08-31T12:26:52Z",
                },
                now,
            )
        )


class TestRemintPush(unittest.TestCase):
    def test_expired_link_pushes_again_same_episode(self):
        import tempfile
        from unittest.mock import patch
        from hermes_finance import sync_health

        calls: list[str] = []
        clock = {"t": datetime(2026, 8, 30, 12, 26, 52, tzinfo=UTC)}

        def _send(ev, **_kw):
            calls.append(ev.key)
            return "sent_push"

        def _now():
            return clock["t"]

        def _mint(_item_id):
            exp = (_now() + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"public_url": "https://example.invalid/r", "expires_at": exp}

        norcal = {
            "item_id": "nymR-test",
            "institution": "1st-northern-california-credit-union",
            "error": "ITEM_LOGIN_REQUIRED",
        }
        cfg = {
            "sync_break_email": False,
            "sync_break_pushover_after_days": 0,
        }
        summary = {"failed_items": [norcal]}

        with tempfile.TemporaryDirectory() as td:
            health_path = Path(td) / "sync_health.json"
            health_path.write_text('{"failures": {}}\n')
            with (
                patch.object(sync_health, "_path", lambda: health_path),
                patch.object(
                    sync_health,
                    "_health_log_path",
                    lambda: Path(td) / "sync_health.log",
                ),
                patch.object(sync_health, "_now", _now),
                patch.object(sync_health, "send_alert", side_effect=_send),
                patch.object(sync_health, "load_config", return_value=cfg),
                patch(
                    "hermes_finance.plaid_sync.item_repair_grace_active",
                    return_value=False,
                ),
                patch(
                    "hermes_finance.plaid_link_server.mint_repair_link",
                    side_effect=_mint,
                ),
            ):
                process_sync_health(summary, dry_run=False)
                clock["t"] = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
                process_sync_health(summary, dry_run=False)  # still inside 24h
                clock["t"] = datetime(2026, 8, 31, 12, 27, tzinfo=UTC)
                process_sync_health(summary, dry_run=False)  # expired

        self.assertEqual(len(calls), 2)
        self.assertNotIn("relink", calls[0])
        self.assertIn("relink", calls[1])


class TestEmailCopy(unittest.TestCase):
    def test_norcal_login_subject(self):
        self.assertEqual(
            break_subject(
                "1st-northern-california-credit-union",
                "ITEM_LOGIN_REQUIRED: the login details of this item have changed",
            ),
            "Budget Bot: NorCal needs a re-login",
        )

    def test_body_has_no_plaid_dump(self):
        err = (
            'Plaid /transactions/sync HTTP 400: {\n  "documentation_url": '
            '"https://plaid.com/docs/errors/item/#item_login_required"'
        )
        body = break_email_body("1st-northern-california-credit-union", err)
        self.assertNotIn("documentation_url", body)
        self.assertNotIn("HTTP 400", body)
        self.assertNotIn("plaid.com", body)
        self.assertIn("NorCal", body)
        self.assertIn("re-login", body.lower())
        with_url = break_email_body(
            "1st-northern-california-credit-union",
            err,
            repair_url="https://example.invalid/hermes-repair-test",
        )
        self.assertIn("https://example.invalid/hermes-repair-test", with_url)
        self.assertIn("24h", with_url)
        self.assertNotIn("Ask the box for an update-mode Link", with_url)

    def test_push_body_plain_english(self):
        body = break_push_body(
            "1st-northern-california-credit-union",
            "ITEM_LOGIN_REQUIRED",
            days=0,
            repair_url="https://example.invalid/hermes-repair-test",
        )
        self.assertEqual(
            body,
            "NorCal needs a re-login. Re-login here (link expires in 24h):\n"
            "https://example.invalid/hermes-repair-test\n",
        )
        self.assertNotIn("LOGIN", body)

    def test_labels(self):
        self.assertEqual(friendly_institution("paypal"), "PAYPAL")
        self.assertEqual(break_kind("stale_transactions last_success=…"), "STALE")


if __name__ == "__main__":
    unittest.main()
