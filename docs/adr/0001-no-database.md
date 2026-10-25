# ADR 0001: No database

- Status: accepted
- Date: 2026-10-25

## Context

The production system evalroom was extracted from kept items, reviews, golden cases, runs, calibration versions and audit events in Postgres through Prisma, scoped by tenant. That coupling made the statistics impossible to reuse elsewhere and hard to test: every test mocked the ORM.

Teams that would use evalroom already have somewhere their outputs and reviews live: a warehouse, a labelling tool, a spreadsheet export, an observability platform.

## Decision

The library ships no database and no ORM. Every computation is a pure function over plain data (`Review[]`, `Rubric`, `CaseResult[]`). Persistence is two small interfaces, `EvalStore` and `CalibrationStore`, plus `AuditSink`, with two implementations each: in memory, and append-only JSONL files using `node:fs`.

## Consequences

- Zero runtime dependencies; the statistics can be tested with literal values.
- Callers bring their own storage by implementing four to twelve async methods.
- No transactions. Activation and rollback touch two calibration versions in sequence; the JSONL store is single-writer. A database-backed store can wrap those calls in a transaction.
- Multi-tenancy is a `tenant` tag plus filters, not enforced isolation. Isolation is the store implementer's job.
- The JSONL store re-reads a file to detect duplicate ids. Fine for thousands of records, not for millions; that is what a custom store is for.
