#!/usr/bin/env python3
"""Safely test a WhatsApp Cloud API message using environment variables.

The script is dry-run by default. Pass --send only when the recipient and
Meta test configuration have been explicitly verified.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request


class WhatsAppApiError(RuntimeError):
    """Raised when the WhatsApp Cloud API rejects the request."""


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise WhatsAppApiError(f"Missing required environment variable: {name}")
    return value


def build_payload(recipient: str, template_name: str, language_code: str) -> dict[str, object]:
    return {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }


def send_message(
    *, access_token: str, phone_number_id: str, api_version: str, payload: dict[str, object]
) -> dict[str, object]:
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "afm-whatsapp-cloud-api-test/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not echo response headers or the Authorization value.
        try:
            detail = json.load(exc)
        except (json.JSONDecodeError, ValueError):
            detail = {"status": exc.code}
        raise WhatsAppApiError(f"WhatsApp API rejected the request: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WhatsAppApiError(f"WhatsApp API request failed: {exc}") from exc

    if not body.get("messages"):
        raise WhatsAppApiError("WhatsApp API returned no message receipt")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send the message; without this flag the script only prints a redacted preview",
    )
    parser.add_argument(
        "--template",
        default=os.environ.get("WHATSAPP_TEMPLATE_NAME", "hello_world"),
        help="Approved WhatsApp template name (default: hello_world)",
    )
    parser.add_argument(
        "--language",
        default=os.environ.get("WHATSAPP_TEMPLATE_LANGUAGE", "en_US"),
        help="Approved template language code (default: en_US)",
    )
    args = parser.parse_args()

    try:
        access_token = required_env("WHATSAPP_ACCESS_TOKEN")
        phone_number_id = required_env("WHATSAPP_PHONE_NUMBER_ID")
        recipient = required_env("WHATSAPP_TO")
        api_version = os.environ.get("WHATSAPP_API_VERSION", "v23.0").strip()

        if not re.fullmatch(r"[1-9][0-9]{7,14}", recipient):
            raise WhatsAppApiError("WHATSAPP_TO must be an international number without '+' or spaces")
        if not re.fullmatch(r"[0-9]{10,20}", phone_number_id):
            raise WhatsAppApiError("WHATSAPP_PHONE_NUMBER_ID must contain digits only")
        if not re.fullmatch(r"v[0-9]+\.[0-9]+", api_version):
            raise WhatsAppApiError("WHATSAPP_API_VERSION must look like v23.0")

        payload = build_payload(recipient, args.template, args.language)
        if not args.send:
            print(json.dumps({"mode": "dry-run", "endpoint_version": api_version, "payload": payload}, indent=2))
            print("No message was sent. Re-run with --send only after verifying the recipient and template.", file=sys.stderr)
            return 0

        response = send_message(
            access_token=access_token,
            phone_number_id=phone_number_id,
            api_version=api_version,
            payload=payload,
        )
        print(json.dumps({"mode": "sent", "messages": response.get("messages", [])}, indent=2))
        return 0
    except WhatsAppApiError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_payload", "main"]
