"""Minimal Plaid REST client (urllib only)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ENV_FILE = Path(os.environ.get("HERMES_PLAID_ENV", "/etc/hermes-finance.env"))


def load_plaid_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_FILE.is_file():
        raise FileNotFoundError(f"missing {ENV_FILE}")
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def plaid_host(env: dict[str, str] | None = None) -> str:
    env = env or load_plaid_env()
    e = (env.get("PLAID_ENV") or "production").lower()
    if e == "sandbox":
        return "https://sandbox.plaid.com"
    if e == "development":
        return "https://development.plaid.com"
    return "https://production.plaid.com"


def plaid_secret(env: dict[str, str] | None = None) -> str:
    env = env or load_plaid_env()
    e = (env.get("PLAID_ENV") or "production").lower()
    if e == "sandbox":
        return env.get("PLAID_SECRET_SANDBOX") or env.get("PLAID_SECRET") or ""
    return env.get("PLAID_SECRET") or ""


def plaid_post(path: str, body: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any]:
    env = env or load_plaid_env()
    payload = {
        "client_id": env["PLAID_CLIENT_ID"],
        "secret": plaid_secret(env),
        **body,
    }
    host = plaid_host(env)
    req = urllib.request.Request(
        host + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        raise RuntimeError(f"Plaid {path} HTTP {e.code}: {err[:800]}") from e


def create_link_token(
    *,
    client_user_id: str = "zavdi-hermes",
    redirect_uri: str | None = None,
    products: list[str] | None = None,
    webhook: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "client_name": "Hermes Finance",
        "language": "en",
        "country_codes": ["US"],
        "user": {"client_user_id": client_user_id},
    }
    if access_token:
        # Update mode: repair an existing Item. Do not send products.
        body["access_token"] = access_token
    else:
        body["products"] = products or ["transactions"]
    if redirect_uri:
        body["redirect_uri"] = redirect_uri
    if webhook:
        body["webhook"] = webhook
    return plaid_post("/link/token/create", body)


def exchange_public_token(public_token: str) -> dict[str, Any]:
    return plaid_post(
        "/item/public_token/exchange",
        {"public_token": public_token},
    )


def transactions_sync(access_token: str, cursor: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {"access_token": access_token}
    if cursor:
        body["cursor"] = cursor
    return plaid_post("/transactions/sync", body)


def accounts_get(access_token: str) -> dict[str, Any]:
    """Account list + balances (dollars float in Plaid API)."""
    return plaid_post("/accounts/get", {"access_token": access_token})


def item_get(access_token: str) -> dict[str, Any]:
    return plaid_post("/item/get", {"access_token": access_token})


def item_webhook_update(access_token: str, webhook: str) -> dict[str, Any]:
    """Point an existing Item at a webhook URL (HTTPS required in production)."""
    return plaid_post(
        "/item/webhook/update",
        {"access_token": access_token, "webhook": webhook},
    )
