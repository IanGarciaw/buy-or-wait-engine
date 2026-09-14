# Token Usage and Cost Analysis

_Generated 2026-09-13 08:13 UTC from the final full-dataset run that produced `output.csv`._

## How the model is used

The financial calculation is **fully deterministic Python**. The model is used only
to turn unstructured evidence into typed facts, and to phrase explanations around
numbers that are already frozen. It never computes an amount, a date or a plan.

Provider: **Anthropic**, accessed through the local `claude` CLI
(`claude --print --output-format json`). No API key is used; the CLI runs against a
subscription plan, so **real billed cost is $0.00**. The `cost` column below is the
list-price equivalent reported by the CLI in `total_cost_usd`, included because the
task asks for an estimated cost. Both figures are stated so neither is misleading.

## Per model

| Provider | Model | Calls | Input tokens | Cache create | Cache read | Output tokens | Cost (list, USD) |
|---|---|---:|---:|---:|---:|---:|---:|
| Anthropic | `claude-haiku-4-5-20251001` | 37 | 461 | 212,475 | 366,840 | 176,294 | $1.4297 |
| **Total** | — | **37** | **461** | **212,475** | **366,840** | **176,294** | **$1.4297** |

## Per pipeline stage

| Stage | Calls | Input tokens | Output tokens | Cost (list, USD) |
|---|---:|---:|---:|---:|
| message_batch | 21 | 243,701 | 163,182 | $1.1341 |
| image | 16 | 336,075 | 13,112 | $0.2956 |

## Totals and per-request averages

- Requests in `dataset/requests.csv`: **250**
- Model calls: **37**  (0.148 per request)
- Input tokens (incl. cache): **579,776**  (2,319.1 per request)
- Output tokens: **176,294**  (705.2 per request)
- Total tokens: **756,070**  (3,024.3 per request)
- Estimated cost (list price): **$1.4297**  (**$0.00572** per request)
- Real billed cost: **$0.00** (subscription, no API key)
- Model wall-clock: **1432.7 s** across 37 calls, run in parallel

## Why the call count is low

Calls are batched, not per request. The CLI carries a fixed per-call overhead of
about 9,100 tokens once `--restricted` is set, so many small calls cost far more
than a few large ones. Messages are extracted in batches; images are one call each
because each is a distinct document. Every result is cached on disk by `image_id`
and `message_id`, so a re-run costs nothing and a resume never repeats work.
