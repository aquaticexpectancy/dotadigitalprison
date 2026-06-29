"""Non-blocking Poke HTTP — httpx async client with warm connection pool."""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://poke.com/api/v1"

_client: httpx.AsyncClient | None = None


def poke_base_url() -> str:
    env = os.environ.get("POKE_API", "").strip()
    return env.rstrip("/") if env else DEFAULT_BASE_URL


async def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
            headers={"User-Agent": "dota-digital-prison/httpx"},
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def post_json(
    url: str,
    *,
    bearer_token: str,
    data: dict[str, Any],
) -> tuple[bool, str, dict[str, Any] | None]:
    client = await get_http_client()
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }
    try:
        response = await client.post(url, json=data, headers=headers)
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and body.get("success") is True:
            return True, "ok", body
        return True, str(body), body if isinstance(body, dict) else None
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:200]
        return False, f"HTTP {exc.response.status_code}: {detail}", None
    except httpx.HTTPError as exc:
        return False, str(exc), None


async def send_webhook_async(
    webhook_url: str,
    webhook_token: str,
    data: dict[str, Any],
) -> tuple[bool, str]:
    ok, detail, _body = await post_json(
        webhook_url,
        bearer_token=webhook_token,
        data=data,
    )
    if ok:
        return True, "webhook fired"
    return False, detail


async def send_api_message_async(api_key: str, message: str) -> tuple[bool, str, dict[str, Any] | None]:
    return await post_json(
        f"{poke_base_url()}/inbound/api-message",
        bearer_token=api_key,
        data={"message": message},
    )
