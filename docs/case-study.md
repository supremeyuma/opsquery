# Case study: OpsQuery

## Problem

An analytics team wants to ask questions about ERP/POS data in plain language. Two failure modes
dominate the market: the assistant can write to the database, and nobody can tell whether the
answers are right. A third matters in practice: retrieved documents are an injection channel, so the
assistant can be told to do things by a supplier PDF.

The deliverable is therefore not a chat UI. It is a read-only query path, a retrieval path that
treats documents as data, and an evaluation suite that fails CI when accuracy, safety or
groundedness regress.

## Approach

1. **Safety before features.** The SQL guard and the read-only role were built before any planner.
   The guard parses exactly one statement, rejects everything that is not a query, enforces table
   and column allowlists, blocks catalogs/file/sleep functions and adds a bounded LIMIT. The role
   has SELECT only, a 5s timeout and read-only transactions; bypassing the guard still cannot write.
2. **A real database, not string matching.** The seed generator produces 256,573 deterministic rows
   over 24 months with seasonality, refunds, duplicate names, NULLs, negative stock and UTC-boundary
   orders. Golden SQL is executed against those rows; a generated query is correct only if its
   result set equals the golden result set (order-insensitive, typed).
3. **Traps instead of easy cases.** 63 scored cases include 15 tagged traps. A naive single-prompt
   planner scores 4/63. The baseline is published, not hidden.
4. **Retrieval with citations and quarantine.** 20 policy documents are chunked losslessly, indexed
   with deterministic feature-hash embeddings in pgvector, retrieved with vector + lexical
   reranking, and every claim must cite a chunk that supports it. Documents containing
   instruction-like text are quarantined as a whole.
5. **Bounded agent loop.** Six tool calls maximum; tools are `run_sql`, `retrieve_docs`, `finish`.
   There is no write tool, and a permanent test asserts that.
6. **Eval as a gate.** `make ci` fails below the recorded execution accuracy, below 100% adversarial
   refusal, on any false execution, below groundedness 0.95 or below recall@5 0.85.

## Results

From `make report` / [`reports/eval.json`](../reports/eval.json) at v1.0.0:

| Metric | Baseline | OpsQuery |
|---|---|---|
| SQL execution accuracy (golden set) | 0.064 (4/63) | 1.000 (63/63) |
| Held-out execution accuracy | - | 1.000 (12/12) |
| Citation groundedness | - | 1.000 |
| Retrieval recall@5 | - | 0.950 (19/20) |
| Unsafe-query refusal rate | 0.000 | 1.000 (15/15) |
| False executions in adversarial set | - | 0 |
| Cost per query / p95 latency | - | $0.0000 / 132 ms |

Per-tag execution accuracy is 1.000 for aggregation, date, filter, group, join, null, top-N and
trap tags. The full breakdown is in the report.

### What failed, and what we did about it

The build's own eval runs are part of the record:

- **First eval run: 61/63.** The planner refused "Which customer names appear more than once?"
  because it only recognized the word "duplicate". It also answered "average satisfaction by ticket
  priority" with a single overall average because the group-by detector looked for "by priority" but
  the question said "by ticket priority". Both were fixed by broadening the intent patterns, then
  the full suite was rerun. These were planner bugs, not eval changes; no threshold was lowered.
- **Retrieval recall is 0.950, not 1.0.** The one miss is "How are chargebacks handled?". The
  chargeback chunk is retrieved outside the top five for that phrasing. It is published in
  `reports/eval.json` as `recall_misses` rather than rounded away.
- **The first groundedness implementation scored 0.25** because a citation placed after a sentence
  was attributed to the next sentence. The fix was structural: citations now sit before the period,
  and the metric was rerun. The 1.000 is a result of that fix, not a threshold change.
- **The demo initially answered the poisoned FAQ with unrelated tax text.** Retrieval quarantine
  worked at chunk level but the clean first chunk of a poisoned document slipped through. Quarantine
  is now document-level.

### Limits — read this before quoting 1.000

- The measured system is a deterministic rules-based planner. It is executed against real rows, but
  it is not a live LLM. A provider-backed model can be plugged into the same loop; its accuracy is
  not what this number reports.
- The cases were authored in the same repository as the planner. The 12 held-out cases were
  reported separately but are not an independent benchmark. The naive baseline and the trap cases
  are the discrimination evidence, not the absolute 1.000.
- The corpus is 20 synthetic policy documents. Recall on a real document estate will be lower.
- The dataset is synthetic. It exercises awkward shapes, not every real ERP quirk.
- Cost is zero because the measured path makes no API calls. Latency is local PostgreSQL time, not
  provider latency.

## Reproduce

```bash
make setup
make db-up
make seed
make eval
make report
make ci
make demo
```

The fresh-clone acceptance is the same sequence from a clean checkout, followed by `make ci`.
