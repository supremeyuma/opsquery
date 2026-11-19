# ADR 0001 — Eval measures the deterministic offline planner

Status: accepted (v1.0.0)

## Context

`PLAN.md` requires execution accuracy measured against real rows and a suite that runs offline. It
also forbids live LLM calls in tests. No provider credentials are available in this environment, and
committing a cassette that merely replays each case's expected SQL would make the metric circular.

## Decision

`make eval` runs the deterministic rules planner (`agent/planner.py`) as the proposer. Its SQL is
executed through the guard and read-only executor and compared to independently written golden SQL.
A naive baseline planner is scored on the same cases. The LLM client and cassette replay exist and
are tested, but the 1.000 accuracy is attributed explicitly to the offline planner in the README and
case study.

The recorded accuracy floor is `1.0`, the measured value at M3. Any regression fails CI. This is a
floor on the measured system, not a claim about a provider-backed model.

## Consequences

- The metric is honest about what it measures and runs without network access.
- The 1.000 is not evidence that an arbitrary LLM will score 1.000; the case study says so.
- When the OpenAI-compatible provider is enabled, a new cassette must be recorded with
  `make record` and the floor re-baselined in a reviewed `feat!:` change with before/after numbers.
