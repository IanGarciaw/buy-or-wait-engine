# Buy or Wait? — solution

An affordability agent for HackerRank Orchestrate. For every request in
`dataset/requests.csv` it decides whether the user should pay in full, pay partially, use
an instalment option, wait, or not proceed — and writes one row per request to
`output.csv` in the repository root.

## Run it

```bash
python3 code/main.py
```

Python 3.10+ (developed on 3.14). **No third-party dependencies** — standard library only.
Nothing to install, nothing to configure.

```bash
python3 code/main.py --samples    # run the 25 solved samples and print the scoreboard
python3 code/main.py --no-facts   # skip all model calls; CSV inputs only
```

Model extraction results ship **already populated** in `code/cache/` — 16 image facts, 215
message facts, and `usage.jsonl` with one line per real model call. That is deliberate: it
lets you reproduce the exact `output.csv` we submitted without spending a single token, and
it is the same file the usage report is computed from.

### If you want to re-run the model extraction

Delete `code/cache/*.json` and run again. Extraction uses the local Anthropic `claude` CLI
(`claude --print --output-format json`); no API key is read or required. If the CLI is not
present the pipeline still runs — it falls back to the cached facts, and if those are gone
too it proceeds without them rather than failing. Every financial rule is enforced either
way, because no rule depends on a model's output: the extractors only supply amounts and
typed facts that deterministic code then validates.

## Design in one paragraph

**The model is not the calculator.** Amounts, dates, eligibility and plans are computed by
deterministic Python. The model is used only where the input is genuinely unstructured:
reading an amount off a payroll document or an invoice, and turning a free-text message
into a typed fact. Explanations are written last, around numbers that are already frozen —
the model can phrase a decision but cannot change one.

## Pipeline

```
dataset/ ──┬── CSV ─────────────────────────────────┐
           ├── images  → multimodal extractor ─┐    │
           └── messages→ semantic extractor ───┤    │
                                          facts.json│
                                               └────┤
                                                    ▼
                                          FINANCIAL RECONSTRUCTION
                                                    ▼
                                             90-DAY FORECAST
                                                    ▼
                                             DECISION ENGINE
                                                    ▼
                                       INDEPENDENT VERIFIER
                                       PASS / REPAIR / BLOCK
                                                    ▼
                                                output.csv
```

| Path | Role |
|---|---|
| `contracts.py` | Frozen types shared by every component. All integration goes through here |
| `loader.py` | Reads the 9 CSVs. Read-only: `dataset/` is never modified |
| `finance/` | Financial reconstruction, recurrence detection, 90-day cash forecast, `amount_safe_to_pay` |
| `decision/` | Eligibility filtering, plan ranking, spending changes, the seven output fields |
| `extraction/` | The only components that call a model. Closed output schemas |
| `evaluation/` | Scorer against the 25 solved samples, independent verifier, mutation tests |
| `release/` | Packaging, exit checklist, usage report generator |
| `main.py` | Orchestrator |

## The core rule

A recommendation is safe only if the user completes the whole plan, covers essential
spending, and stays at or above `minimum_balance_to_keep` for the entire 90-day horizon.

```
amount_safe_to_pay = min( requested_amount,
                          max(0, min_projected_balance_90d - minimum_balance_to_keep) )
```

computed before optional spending changes. What governs is the **lowest point of the
forecast curve**, not today's balance: a user with a healthy balance today and rent due on
day 28 has less room than the balance suggests.

Cash-state rules, taken literally from the statement: pending debits are reserved; pending
credits are never counted; scheduled events land on their settlement date; cancelled and
failed rows are dropped; unrealized investment value is never cash; foreign-currency
amounts are converted with the rate row for their settlement date.

Plan selection filters **eligibility first** (the user's accepted payment methods and
`max_installment_months`), then ranks the survivors by the statement's six criteria in
order: completes by the deadline, needs no spending changes, minimises total paid, starts
earlier, fewer payments, lowest `payment_option_id`.

## The verifier is independent, on purpose

`evaluation/verifier.py` re-simulates the recommended plan with **its own implementation**
of the 90-day forecast, written separately from `finance/`. If the two implementations
disagree, the row is not written and the discrepancy is recorded.

The reason is simple: if the code that computes the answer is also the code that checks it,
a sign error validates itself. Two implementations that share a blind spot produce a false
green that nothing catches. This costs a second forecast implementation, and it buys the
only real defence against being wrong consistently across all 250 rows at once.

The verifier has exactly three outcomes. `REPAIR` is reserved for unambiguous mechanical
fixes — date format, rounding, capping `safe` at `requested`, making two partial payments
sum exactly. It is never used to reinterpret the financial problem: a substantive
inconsistency produces `BLOCK` and falls back to the conservative answer.

`evaluation/mutaciones.py` calibrates the verifier by feeding it deliberately invalid
decisions and requiring that it catch every one. A checker that has only ever been seen
green has not demonstrated that it knows how to go red.

## Scope of effect ≤ scope of evidence

A message may clarify, amend, cancel, delay or confirm a financial fact — but it may never
change more of the financial state than its own evidence supports. That principle is enforced,
not assumed.

The case that made it explicit: a payroll message reading *"One household employment record has
ended. **The remaining confirmed monthly salary is EUR 1628**"* carries no `related_event_id`,
because no single supplied row describes it. Reading that absence as "terminate everything"
switched off the very salary the message confirms, in six different users.

`extraction/alcance.py` classifies the semantic scope of a termination and returns a symbol
from a closed set declared in `contracts.py`. Message text remains untrusted data: no path
turns it into an instruction. The precedence is explicit — targeted event, then identified
source, then declared end of employment (which reaches employment income only, never an
investment distribution or a refund), and otherwise no widening at all.

Three properties hold it in place, and each one was observed failing before it was observed
passing: confirmed income survives; a declared end of employment is not projected; income
unrelated to employment survives. Switching the rule off for a plain "terminate nothing"
produces the same six output rows on this corpus — and fails the second property. Identical
output is not identical behaviour.

## Untrusted evidence

Messages and images are data, never instructions. The extractors can only emit values from
a **closed schema** — there is no field through which an instruction could travel. `kind`
must be one of the ten allowed fact types; anything else is discarded in Python after the
call. A `target_event_id` that does not exist in the dataset is discarded.

The last line of defence is structural: **the verifier never reads messages or images.**
Even if an extractor were talked into something, the row still has to pass a verifier that
does not know the message exists. `extraction/test_injection.py` demonstrates this against
a crafted prompt-injection message.

## Evaluation workflow

The 25 rows of `sample_requests.csv` belong to `user_01..user_25`; the 250 evaluation
requests belong to `user_26..user_275`. The overlap is zero, which makes the samples a
clean labelled calibration set with no leakage.

```bash
python3 code/main.py --samples             # scoreboard + error classes
python3 code/evaluation/mutaciones.py      # verifier calibration; 32 mutations, non-zero exit if one escapes
python3 code/evaluation/fixtures/test_piso.py   # proves the 90-day floor actually decides
bash code/release/empaquetar.sh            # exit checklist (8 checks) + build code.zip
```

The scoreboard reports `status`, `safe ±1%`, `method`, `plan` and `earliest` out of 25, plus
a breakdown of failures by **error class** (recurrence, income forecast, pending handling,
eligibility, payment ranking, currency/date, rounding…). Development was driven by fixing
classes of error, not individual cases: a change that fixes four samples through one
general rule is worth keeping; a change that only fixes one is overfitting.

No `request_id`, `user_id` or sample answer appears anywhere in the source. The samples are
used to discover rules, never as labels.

## Cost

`evaluation/usage_report.md` is generated from `code/cache/usage.jsonl`, which records one
line per model call at the moment it happens. Nothing in that report is estimated.

## Measured results

Scored against the 25 solved samples (`user_01..user_25`), which are disjoint from the 250
evaluation requests (`user_26..user_275`) — overlap is zero, so there is no leakage.

Re-measured on 2026-09-13 against the frozen candidate (`8004018`, tag `RC5`) with
`python3 code/main.py --samples`:

```
STATUS         17 / 25
SAFE  (±1%)     6 / 25        (±5%: 14/25   ±10%: 15/25   median relative error 4.3%)
METHOD         18 / 25
PLAN           17 / 25
EARLIEST       18 / 25
EXACT ROWS      4 / 25
CHANGES        22 / 25        (not on the board, but scored in the evaluation)
```

The ±5%, ±10% and median-error figures come from an earlier run and were not re-measured
today; the six counters above were. The "explanations byte-identical: 15/25" line that used
to sit here was not reproducible with the current scoreboard and has been removed rather
than restated.

Three of those counters are lower than they were two tags ago, and it is worth being exact
about where the points went, because it is easy to blame the wrong change:

| | RC3 (`d9b781d`) | RC4 (`e74110d`) | RC5 (`8004018`) | RC6 | **RC7** |
|---|---|---|---|---|---|
| STATUS | 20/25 | 17/25 | 17/25 | 17/25 | **17/25** |
| SAFE ±1% | 6/25 | 6/25 | 6/25 | 6/25 | **6/25** |
| METHOD | 21/25 | 18/25 | 18/25 | 18/25 | **18/25** |
| PLAN | 20/25 | 17/25 | 17/25 | 17/25 | **17/25** |
| EARLIEST | 18/25 | 16/25 | 18/25 | 18/25 | **18/25** |
| EXACT ROWS | 4/25 | 4/25 | 4/25 | 4/25 | **4/25** |

All three were scored on 2026-09-13 with the same `dataset/` and the same `code/cache/`.
**The three points were lost in RC3 → RC4**, when the 90-day window was restored and the
safety floor became a hard filter. **RC5 costs nothing and gains +2 on EARLIEST.**

Fed the ground truth's `amount_safe_to_pay` and `earliest_date_for_full_payment`
(`python3 -m decision.calibrar`, oracle mode), the decision layer now scores
`status 20 · method 20 · plan 20 · safe 25 · earliest 25 · changes 24` out of 25. Before the
safety floor became a hard filter it scored 25/25 on all six; removing only that veto from
`decision/ranking.py::mejor()` restores 25/25 on all six, which is how we know the veto is
the whole difference and not a forecast regression. That gap of 5 is the measured, deliberate
price of applying the statement's rule literally — see the section on the floor below.

The estimator in use is the median of the 8 most recent occurrences × 1.06, applied to
*variable debits only*. It was not chosen by taste: a sweep of estimator × multiplier ×
lookback was scored end-to-end on the 25 samples and this combination won. `1.12` scores
one more exact row but costs two on STATUS and four on EARLIEST; `lookback=0` costs one on
SAFE. Both were measured and rejected.

### What we could not crack, stated plainly

Four cases fail structurally rather than by calibration. For `request_05` the ground truth
implies exactly 32,638.10 of outflow over the horizon, while the sum of each series' *own
historical minimum* already exceeds that. No estimator in the family can reach it. We ruled
out, with numbers: shorter horizons (75–86 days give identical results — a plateau, not a
peak), a day-zero boundary rule (two samples with the same weekly series and the same gap
resolve differently), dropping any single series (no subset closes the gap), and a monthly
aggregate. Rather than fit a constant to close one case, we left it open: a rule that only
fixes one sample is overfitting, and the statement's checklist forbids file-specific answers.

### Two defects the sample scoreboard could not see

Both were found by reasoning about the 250, not by chasing the 25, and neither moved the
scoreboard because no sampled user was affected:

1. **Horizon overflow.** Occurrence generation ran to 200 monthly instances (reaching year
   2042) instead of stopping at 90 days. The cash curve never noticed — it ignores flows
   outside the window — but two consumers did: the flexible-expense notes reported 200 and
   400 occurrences instead of 3 and 4, so the decision layer saw 66× the real saving from
   stopping a subscription.
2. **`not_yet_cash` on a debit inverted the rule.** Six evaluation users have a pending
   *card charge* whose message says a reversal has not been posted. The money is still out.
   Discarding it left us optimistic exactly where the bank warns of risk.

### The 90-day floor is a hard filter, and what that cost

The statement says three times that a plan is safe only if the balance never falls below
`minimum_balance_to_keep`, and line 189 says what to do when none is: *"`not_recommended` is
the fallback when no safe eligible payment is available. When more than one eligible plan is
safe, rank the plans in this order: …"*. The six ranking criteria only ever apply to safe
plans.

An audit found the check existed in the code and was never invoked: the only real filter was
"each payment ≤ amount_safe_to_pay", which leaves no ceiling on how far a plan may sink.
That was first fixed by making the floor the first *key of the ranking order* — better than
nothing, but still not a gate: when no clean plan existed, the one that broke the floor
*least* was recommended anyway.

**Since RC4 (`e74110d`) the floor is a veto, not a tie-breaker.** `decision/ranking.py::mejor()`
drops every plan with a gap before ranking; if nothing survives, the answer is
`not_recommended`.

Measured on the frozen candidate (`8004018`) on 2026-09-13: removing only that veto and
re-running `python3 code/main.py` changes **38 of the 250 rows**, every one of them from
`not_recommended` to `installments` (`not_affordable` → `affordable_with_plan`). Those 38 are
the unsafe recommendations the gate prevents. Control: without the mutation the regenerated
`output.csv` is byte-identical to the delivered one (sha256 `9167bb84…05fc77`), so the
counter can tell 0 from 38.

The price is real and was paid knowingly. In oracle mode — fed the ground truth's own
`amount_safe_to_pay` and `earliest_date_for_full_payment` — the veto takes the decision layer
from 25/25 on all six fields down to `status 20 · method 20 · plan 20 · safe 25 · earliest 25
· changes 24`. The plans it rejects there are the reference answers themselves: `request_03`,
paying on exactly the date the ground truth gives as its `earliest_date_for_full_payment`,
breaks our floor by 173% of the minimum.

That tension is the most useful thing we learned and we do not paper over it: **our 90-day
curve is probably not the curve that generated the reference answers.** Two readings were
possible — defer to the reference answers, or obey the sentence in the statement. We chose
the sentence: a contract we can read beats a scoreboard we can only guess at. The cost is
stated above in full so a reader can disagree with the choice on the numbers, not on the
framing.

Two other resolutions worth recording, both driven by the same discipline:

- A tolerance band was measured rather than guessed. The minimum tolerance that would let
  `request_02`'s reference plan pass is **36.24%** of `minimum_balance_to_keep` — that is not
  a band, it is switching the floor off under another name. Discarded.
- The six `Possible duplicate card charge` rows put two statement rules in direct conflict:
  "ignore duplicate records" against "reserve pending debits". The statement settles it
  itself — the financially safer reading — and for a debit that means counting it. The
  linked message confirms the money is still out: *"a reversal has not been posted"*. None of
  the 25 samples contains this case, so the corpus cannot falsify either reading; the
  conservative one stands.
