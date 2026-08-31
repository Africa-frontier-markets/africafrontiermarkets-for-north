#!/usr/bin/env python3
"""Fetch recent Northflank runtime logs for external monitoring.

Required environment variables:
  NORTHFLANK_API_TOKEN
  NORTHFLANK_PROJECT_ID
  NORTHFLANK_SERVICE_ID

Optional:
  NORTHFLANK_DEPLOYMENT_ID
  NORTHFLANK_LOG_LINE_LIMIT (default: 100)
  NORTHFLANK_LOG_DURATION_SECONDS (default: 900)
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def main() -> int:
    token = required("NORTHFLANK_API_TOKEN")
    project_id = required("NORTHFLANK_PROJECT_ID")
    service_id = required("NORTHFLANK_SERVICE_ID")
    params: list[tuple[str, str]] = [
        ("type", "runtime"),
        ("duration", os.getenv("NORTHFLANK_LOG_DURATION_SECONDS", "900")),
        ("lineLimit", os.getenv("NORTHFLANK_LOG_LINE_LIMIT", "100")),
        ("direction", "backward"),
    ]
    deployment_id = os.getenv("NORTHFLANK_DEPLOYMENT_ID")
    if deployment_id:
        params.append(("deploymentId", deployment_id))

    url = (
        "https://api.northflank.com/v1/projects/"
        f"{project_id}/services/{service_id}/logs?{urlencode(params)}"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except Exception as exc:  # noqa: BLE001
        print(f"northflank log probe failed: {exc}", file=sys.stderr)
        return 2

    logs = payload.get("data", [])
    print(json.dumps({"service_id": service_id, "count": len(logs), "logs": logs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
