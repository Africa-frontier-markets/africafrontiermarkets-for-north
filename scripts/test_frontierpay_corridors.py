#!/usr/bin/env python3
"""Test public et non financier des cotations FrontierPay/Kora.

Le script appelle uniquement l'endpoint public de simulation. Il ne crée
aucune transaction, ne fait aucun payout et n'utilise aucune clé secrète.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

CORRIDORS = [
    ("ci-ghana", "XOF", "GHS"),
    ("ci-nigeria", "XOF", "NGN"),
    ("benin-nigeria", "XOF", "NGN"),
    ("cameroon-nigeria", "XAF", "NGN"),
    ("cameroon-ivory-coast", "XAF", "XOF"),
    ("ivory-coast-cameroon", "XOF", "XAF"),
]


def call(url: str, payload: dict, timeout: float) -> tuple[int, dict | str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, raw
    except (urllib.error.URLError, TimeoutError) as error:
        return 0, str(error)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test des cotations FrontierPay sans mouvement de fonds")
    parser.add_argument(
        "--url",
        default="https://africafrontiermarkets.com/api/v1/public/frontierpay/simulate",
        help="URL de l'endpoint public de simulation",
    )
    parser.add_argument("--amount", type=int, default=100000, help="Montant de test dans la devise source (minimum 100000)")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", help="Écrire le rapport JSON à cet emplacement")
    args = parser.parse_args()

    if args.amount < 100000:
        parser.error("--amount doit être supérieur ou égal à 100000")

    results = []
    for corridor, source, beneficiary in CORRIDORS:
        payload = {
            "amount": args.amount,
            "source_currency": source,
            "beneficiary_currency": beneficiary,
            "corridor": corridor,
            "direction": "payout",
            "metadata": {"execution_mode": "public_preview", "automated_check": True},
        }
        status, body = call(args.url, payload, args.timeout)
        successful = status == 200 and isinstance(body, dict) and body.get("simulation_only") is True
        detail = body.get("detail") if isinstance(body, dict) else str(body)
        results.append({
            "corridor": corridor,
            "source_currency": source,
            "beneficiary_currency": beneficiary,
            "http_status": status,
            "successful_preview": successful,
            "detail": detail,
            "simulation_only": body.get("simulation_only") if isinstance(body, dict) else None,
            "rate_source": body.get("rate_source") if isinstance(body, dict) else None,
        })
        state = "OK" if successful else "FAIL"
        print(f"{state:4} {corridor:24} HTTP {status}: {detail}")

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": args.url,
        "amount": args.amount,
        "financial_action": False,
        "results": results,
    }
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    failures = len([item for item in results if not item["successful_preview"]])
    print(f"\n{len(results) - failures}/{len(results)} corridors opérationnels; {failures} en échec.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
