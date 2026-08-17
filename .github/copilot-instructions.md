# AFM coding agent instructions

## Scope

Work only on an issue that has been explicitly approved by a maintainer and labeled `fix-approved`. Treat the incident form, attached logs, acceptance criteria, and reproduction steps as the source of truth.

## Required behavior

1. Inspect the relevant code and reproduce the issue when possible.
2. Make the smallest safe change; do not refactor unrelated code or infrastructure.
3. Add or update deterministic tests for every behavior changed.
4. Run the repository checks and report their exact results in the pull request.
5. Open a pull request from a new branch. The pull request must remain draft until a maintainer reviews it.
6. Include a summary, root cause, changed files, test results, risk assessment, rollback plan, and deployment notes.

## Safety rules

Never merge, approve, publish, deploy, modify production secrets, change DNS, or alter Northflank resources. Never copy credentials or tokens into issues, logs, commits, or pull requests. If the requested change affects database schema, authentication, payments, trading, or production infrastructure, stop and request explicit maintainer review in the pull request.
