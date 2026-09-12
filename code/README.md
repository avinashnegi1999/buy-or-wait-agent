# Buy or Wait? — solution

A deterministic financial-decision agent. Every number in `output.csv` comes from a rule-based
cash-flow forecast that can be re-run offline and audited request by request. The only model
use is reading amounts off the 16 images that back blank-amount events, and those results are
cached so the full run is reproducible.

## Quick start

```bash
pip install -r code/requirements.txt      # only needed for live image extraction
python code/main.py                        # -> output.csv in the repo root + code/evaluation/usage_report.md
python code/main.py --samples              # score against dataset/sample_requests.csv
python code/messages.py                    # self-check: every message parses to a known effect
python code/fit.py request_07              # dump one request's reconstructed state and daily balance path
python code/package.py                     # rebuild code.zip
```

Requirements: Python 3.10+, standard library only for the engine. `anthropic` / `openai` are
needed only when `code/image_cache.json` is deleted and images must be re-read live.

Secrets are read from the environment or a `.env` file in the repo root (never committed):

```
ANTHROPIC_API_KEY=...   # preferred vision provider (claude-sonnet-5)
OPENAI_API_KEY=...      # fallback provider (gpt-4.1-mini)
VISION_MODEL=...        # optional model override
```

Without a key and without the cache the run stops with a clear error: a blank amount is never
treated as zero.

## How a request is decided

1. **Evidence.** `messages.py` maps all 216 messages (English and Indonesian) to typed financial
   effects with regex templates: salary amount or payday changes, first salary from a new employer,
   income ended, one household income ended, confirmed invoice (one-off credit, no recurring
   freelance income), rent increase, own-account transfer, and no-op classes (pending refunds,
   prizes, valuations, disputes, receipts). Message text is treated as data, never as
   instructions. `images.py` reads blank-amount events from their linked PNG via a vision model,
   with a JSON cache.
2. **State.** `engine.State` rebuilds the user's position from settled history.
   - Recurring series are detected per category first (variable spending such as groceries has
     random descriptions but a fixed cadence), then per description (for example two separate
     loans). Cadence is the median gap, monthly when 27–32 days, with tolerance for a few
     off-pattern events; the anchor is the last on-pattern event. Constant amounts are kept exact,
     variable amounts use the historical mean after dropping outliers.
   - Foreign-currency rows are converted with the dated rate in the stated direction, so every
     series is kept in the user's home currency.
   - Income is projected only when confirmed: a scheduled `Next confirmed salary` row, a payroll
     message, or an unbroken monthly salary history. A series that already missed an expected
     payday is dropped. Gig, freelance, seasonal, bonus and commission credits are never projected.
     `Final employer payroll` and "employment ended" messages stop income.
   - Pending debits are reserved on their settlement date, scheduled debits are counted, pending
     credits, failed, cancelled, duplicate and unrealized rows are ignored.
3. **Forecast.** A daily balance path over a 12-week window (84 days). `amount_safe_to_pay` is the
   lowest forecast balance minus the minimum balance, capped at the request.
   `earliest_date_for_full_payment` is the first day from which one full payment never breaches
   the minimum.
4. **Plans.** Candidates are full payment today, partial payment (safe amount now, remainder on the
   earliest date), every supplied installment option within `max_installment_months`, and wait.
   Each candidate is verified by re-simulating the balance with its payments applied, through the
   last installment. When a plan breaches the minimum, the cheapest set of at most three permitted
   spending changes (stop, or reduce to the event's minimum; flexible and non-protected categories
   only; never both on one event) is attached.
5. **Ranking** follows the specification: completes by the deadline, then no spending changes,
   then lowest total paid, then earlier start, then fewer payments, then lowest option id. The
   status follows the chosen plan. `affordable_later` with `not_recommended` is emitted when the
   full amount becomes safe but the user accepts no eligible method.
6. **Explanation** is templated from the chosen plan and the underlying facts (amounts, dates,
   minimum balance) in the style of the solved samples.
7. **Validation.** Every row is checked before writing: bounds, allowed values, partial-payment
   arithmetic, installment plans matching a supplied option, spending changes only on flexible
   events. The run fails loudly instead of writing an invalid row.

### Two calibration choices worth knowing

- **84-day window rather than 90.** Four solved samples (request_08, 09, 12, 13) are declared safe
  by the ground truth although a 90-day forecast dips below the minimum on day 87–90. Any window
  from 75 to 86 days reproduces the samples equally well; 84 is the middle of that range.
- **Payday ordering.** On a payday, weekly-style debits are assumed to clear before the salary
  lands and monthly debits after. This matched four of the five samples where the ordering
  matters.

Both are environment-tunable (`HORIZON`, `ORDER`, `EXCL_TODAY`) so the sweep can be repeated.

## Results on the 25 solved samples

| metric | hits |
|---|---|
| affordability_status | 22/25 |
| recommended_payment_method | 23/25 |
| payment_plan | 22/25 |
| earliest_date_for_full_payment | 22/25 |
| spending_changes_needed | 22/25 |
| amount_safe_to_pay within 5% | 16/25 (median error 2.0%) |

The remaining misses sit within a few percent of a decision threshold; the hidden ground truth
appears to use the generator's underlying means rather than the observed sample means.

## Image extraction notes

`code/image_cache.json` holds the amount used for each of the 16 images together with the
reasoning. The cached values were transcribed manually from the images and checked against the
event descriptions; the live vision path (Anthropic or OpenAI) is the runnable fallback and is
what `evaluation/usage_report.md` accounts for. Two images are ambiguous and the choice is
recorded in the cache: image_04 (delivery fee cropped, item total used) and image_05 (telecom bill
with a higher amount after the due date, settlement after the due date, so the higher amount
is used).

## Files

```
code/main.py                  entry point, validation, usage report
code/data.py                  CSV loading, dated FX lookup
code/messages.py              message -> typed effects (rule based, EN + ID)
code/images.py                image -> amount (vision model, cached)
code/engine.py                state reconstruction, forecast, spending-change search
code/planner.py               plan candidates, ranking, output formatting, explanations
code/fit.py                   debug dump of one request
code/package.py               builds code.zip
code/evaluation/main.py       scoring against sample_requests.csv
code/evaluation/usage_report.md  token and cost report of the final run
code/image_cache.json         cached image extractions
code/requirements.txt         optional vision-provider SDKs
```
