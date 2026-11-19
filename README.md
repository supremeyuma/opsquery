# OpsQuery — retrieval + text-to-SQL agent

Repository scaffold. **Status: M0 — plan only, no implementation yet.**

Ask a question about operational data in plain English. Get a correct, cited, read-only answer —
or a refusal.

- Build plan: [PLAN.md](PLAN.md)
- Agent rules: [AGENTS.md](AGENTS.md)

## Why this exists

Most "chat with your data" demos have two problems: they can write to your database, and nobody can
tell whether the answers are right. This repo treats both as the product — a hard read-only safety
layer, an injection-resistant retrieval path, and an evaluation suite that gates CI the same way a
test suite gates a release.

Domain: ERP/POS data for a small business (customers, orders, invoices, inventory, policies) — the
same schema shape as the systems in production.

## Target results (to be filled from `make eval`, never typed by hand)

| Metric | Baseline | OpsQuery | Status |
|---|---|---|---|
| SQL execution accuracy (golden set) | single-prompt | — | not measured |
| Answer correctness | no retrieval | — | not measured |
| Citation groundedness | — | — | not measured |
| Unsafe-query refusal rate | 0 guardrails | — | not measured |
| False executions in adversarial set | — | — | not measured |
| Cost per query / p95 latency | — | — | not measured |

## Not in scope

- Writing anything to the database. Ever. There is no write path to build.
- A prettier chat UI than a single page is required to demo it.
- Fine-tuning. Prompt/context engineering and retrieval quality first; fine-tuning is a later,
  measured decision.
