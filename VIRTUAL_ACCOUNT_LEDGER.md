# AFM Virtual Account Ledger — Transition Model

## Scope

This additive schema gives each authenticated AFM client one virtual account while the external master account remains an operational omnibus account. It introduces no public order, transfer, deposit or withdrawal endpoint.

| Layer | Responsibility | Access |
|---|---|---|
| `users` | AFM identity, including the mobile OAuth subject | Private |
| `virtual_accounts` | One AFM virtual account per user | Private bearer token |
| `virtual_positions` | Current per-client quantity and average-cost projection | Private bearer token |
| `virtual_ledger_entries` | Immutable cash, execution, fee, corporate-action and reconciliation entries | Private bearer token |
| Trading market data | Read-only prices used to value virtual positions | Public market-data routes |

## Safety constraints

The mobile application receives only the authenticated user’s virtual-account projection. First access may provision the empty AFM account record but never triggers an external trading-provider call. The routes are read-only: they expose no order endpoint or transaction-creation capability. Market valuations are derived from the existing Trading read adapter; unavailable prices remain unavailable rather than being invented.

## Reconciliation boundary

The virtual ledger is the client allocation record. A future operations-only reconciliation process will compare aggregate virtual cash and positions with the master account and create controlled back-office entries; it is deliberately outside mobile and public market routes.
