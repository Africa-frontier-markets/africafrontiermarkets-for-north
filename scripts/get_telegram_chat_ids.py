#!/usr/bin/env python3
"""Print Telegram chat metadata from recent updates without printing the bot token."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class TelegramApiError(RuntimeError):
    pass


def call_api(token: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}{query}",
        headers={"Accept": "application/json", "User-Agent": "afm-telegram-chat-id-workflow/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TelegramApiError(f"Telegram API request failed for {method}: {exc}") from exc
    if not payload.get("ok"):
        raise TelegramApiError(f"Telegram API rejected {method}: {payload.get('description', 'unknown error')}")
    return payload


def extract_chats(updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chats: dict[str, dict[str, Any]] = {}
    for update in updates:
        message = update.get("message") or update.get("channel_post")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat:
            continue
        chat_id = str(chat["id"])
        chats[chat_id] = {
            "chat_id": chat["id"],
            "type": chat.get("type"),
            "title": chat.get("title"),
            "username": chat.get("username"),
        }
    return sorted(chats.values(), key=lambda item: str(item["chat_id"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-webhook", action="store_true", help="Allow reading updates when Telegram webhook is active")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN is required", file=sys.stderr)
        return 2

    try:
        webhook = call_api(token, "getWebhookInfo")["result"]
        webhook_url = webhook.get("url") or ""
        if webhook_url and not args.allow_webhook:
            print("Telegram webhook is active; refusing getUpdates to avoid conflicting consumers.", file=sys.stderr)
            print("Run the workflow with allow_webhook=true only after confirming this is safe.", file=sys.stderr)
            return 3

        updates = call_api(token, "getUpdates", {"limit": 100, "timeout": 0})["result"]
        chats = extract_chats(updates)
        print(json.dumps({"webhook_active": bool(webhook_url), "chats": chats}, ensure_ascii=False, indent=2))
        if not chats:
            print("No chats found. Send /start to the bot or a test message to the target group/channel, then rerun.", file=sys.stderr)
        return 0
    except TelegramApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["extract_chats", "main"]
