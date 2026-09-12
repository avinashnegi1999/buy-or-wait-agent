# Second-opinion review: Buy or Wait?

Reviewed 13 September 2026 against the working tree, not just the supplied review prompt. Production code, `output.csv`, and `code.zip` were not modified. Proposed diffs below are unapplied. Added `review_second_opinion_check.py` as the runnable audit.

The main issue is the 84-day forecast: **21 submitted recommendations materially breach the minimum under the same engine extended to 90 days**. This is a specification violation, but changing it reduces public-sample accuracy. Hidden-score improvement cannot be established without the hidden labels.

All 250 stored predictions reproduce exactly. Structural checks, exact installment schedules, spending permissions, partial arithmetic, and numeric/date/currency references in explanations pass. Five partial plans have separate sub-cent safety breaches after rounding. There are **215 messages**, not the 216 stated in the review prompt; all parse.

Run all repeatable evidence from the repository root:

```powershell
python review_second_opinion_check.py
python code/main.py --samples
python code/messages.py
```

The audit reports defects and exits successfully when its reproduction assertions hold; exit zero does **not** mean the submission has no findings. It uses cached image amounts and makes no model calls. Savings alternatives are compiled in memory. It does not overwrite submission artifacts.

## Findings, ordered by observed output impact, then latent contract risk

### 1. The default horizon omits six days required by the specification

**Location:** `code/engine.py:13`, `State.__init__` at line 167; related claims in `code/planner.py:159,171` and `code/main.py:124`.

The statement explicitly requires the next 90 days. Defaulting to 84 omits future obligations and can recommend payments that breach the minimum. Explanations nevertheless promise 90 days of protection.

**Impact:** Extending only the horizon changes 22/250 statuses, 16 methods, 16 schedules, 22 earliest dates, 6 spending-change fields, and 18 safe amounts. Replaying existing recommendations gives 21 material breaches and five additional sub-cent breaches. These are changes or safety failures under this forecast, not counts of guaranteed hidden-label errors.

Evidence from `python review_second_opinion_check.py`:

```text
SAFETY_90_COUNTS strict= 26 material= 21
SWEEP 90 hybrid sample(status,method,plan,earliest,changes,amount5%)= [19, 20, 19, 18, 21, 14]
250_changed(status,method,plan,earliest,changes,amount)= [22, 16, 16, 22, 6, 18]
```

Material breaches affect requests **30, 32, 33, 40, 44, 59, 75, 123, 137, 158, 165, 174, 185, 201, 203, 213, 215, 232, 237, 255, 265**. The checker prints the date and home-currency shortfall for each. For example, request_40 breaches by EUR 25.61615385 on 2024-09-04; request_123 breaches by IDR 5,110,426.71363078 on 2026-04-05.

Minimal specification fix:

```diff
--- a/code/engine.py
+++ b/code/engine.py
@@
-HORIZON = int(os.environ.get("HORIZON", "84"))  # ponytail: 12-week window fits the solved samples better than 90 (see evaluation)
+HORIZON = int(os.environ.get("HORIZON", "90"))
```

Update the corresponding README and usage-report description before regenerating artifacts. A submission run must use 90 even if the override is retained for experiments.

**Judgment:** Recommend 90 for conformance. Keeping 84 is an explicit sample-fitting gamble. The samples demonstrate a disagreement with this reconstruction, not that the organizer's hidden horizon is 84; recurrence estimates and income treatment are other possible causes. Do not change horizon and payday ordering together and attribute the result to either one.

### 2. Rounding a safe partial payment upward breaks the computed minimum

**Location:** `code/planner.py:52,67–68,114`; `fmt_num` / `fmt_amt`.

The planner uses unrounded headroom, then rounds the first payment to the nearest cent during serialization. If it rounds upward, the emitted plan spends more than the computed safe maximum. Partial plans also bypass `State.is_safe` when added.

**Impact:** 5/11 partial plans, or 5/250 rows. All deficits are below half a cent, so likely score impact is small unless validation is strict. This is distinct from the material horizon breaches.

Evidence from the checker:

```text
SAFETY_84_STRICT [
 ('request_46', '2024-12-13', 0.00093846),
 ('request_138', '2026-04-13', 0.00311111),
 ('request_210', '2026-04-13', 0.00366667),
 ('request_233', '2025-11-14', 0.00461539),
 ('request_273', '2026-04-12', 0.00066667)]
```

Minimal fix: quantize the capacity downward before constructing either partial payment. The remaining amount still rounds to the exact complement of the first payment.

```diff
--- a/code/planner.py
+++ b/code/planner.py
@@
 from datetime import timedelta
+from decimal import Decimal, ROUND_DOWN
@@
-    safe = min(R, st.safe_today())
+    safe = float(Decimal(str(min(R, st.safe_today()))).quantize(
+        Decimal("0.01"), rounding=ROUND_DOWN))
```

An in-memory run of this change produced `unsafe_partial_plans []`. It changes 87 safe-amount fields by at most one cent; most have no recommendation change. The candidate replay guard proposed in finding 5 additionally protects against future partial/wait safety bugs.

### 3. One wait explanation says an earlier payment is impossible when spending changes make it safe

**Location:** `code/planner.py:192–193`, `explain`.

For request_144, waiting is correctly preferred because it needs no cuts. But “Paying earlier would take the balance below the USD 600 minimum” omits that the engine found a safe full payment today after stopping `event_13239` and reducing `event_13306` to USD 17.50.

**Impact:** 1/250 explanations. The method and plan do not need to change.

Evidence from the checker:

```text
WAIT_WITH_SAFE_EARLIER_CUTS ['request_144']
```

The selected plan is `2026-07-15:233.34`. The permitted earlier changes concern the Audio streaming plan and Neighbourhood restaurant. This is a newly verified omission, separate from the first review's payment-preference explanation fixes.

```diff
--- a/code/planner.py
+++ b/code/planner.py
@@
-        return (f"Pay {money(cur, R)} in full on {longdate(earliest)}. Paying earlier would take the balance "
+        return (f"Pay {money(cur, R)} in full on {longdate(earliest)}. Without spending changes, paying in full earlier would take the balance "
                 f"below the {mn} minimum.")
```

### 4. Spending-change selection does not implement the stated savings objective

**Location:** `code/engine.py:420–435`, `State.cheapest_changes`.

The outer loop exits as soon as any one-change solution exists. It therefore minimizes **count first**, then the sum of per-occurrence savings within that count. It does not minimize total monthly savings first. Also, savings for a 21-day expense and a monthly expense are summed without cadence normalization.

**Impact:** Removing only the early exit changes **0/250 current rows**. Normalizing savings to a 30-day month and removing the exit changes spending choices/explanations on **4/250 rows: request_55, request_89, request_99, request_231**. The specification does not mandate this secondary savings objective, so these four changes are policy-sensitive, not proven hidden-score improvements.

To separate selection from forecast errors, the checker adjusts the balance of samples 06/11/21 until computed headroom equals the sample's headroom. This is a **diagnostic intervention only**, never a prediction rule:

```text
GLOBAL_SAVINGS current_250_changed= 0
ALIGNED_HEADROOM request_06 current= (19.0, 1, ['event_476']) without_break= (19.0, 1, ['event_476'])
ALIGNED_HEADROOM request_11 current= (684072.5477777778, 1, ['event_989']) without_break= (684072.5477777778, 1, ['event_989'])
ALIGNED_HEADROOM request_21 current= (47.0, 1, ['event_1816']) without_break= (34.5, 2, ['event_1815', 'event_1816'])
```

Sample 21 explicitly chooses a USD 11 stop plus a USD 23.50 reduction over stopping the USD 47 subscription. Fewest-changes-first and unconditional stop-first therefore do not explain that example. A global least-sacrifice objective fits all three change selections after controlling for headroom; normalizing by cadence preserves those three selections too. This does not fix their full decision mismatches automatically.

Smallest fix for the claimed global objective:

```diff
--- a/code/engine.py
+++ b/code/engine.py
@@
-            if best:
-                break
         return best
```

If retaining the **monthly** wording, also normalize the cost:

```diff
@@
-                    saving = sum(c[3] for c in combo)
+                    saving = sum(c[3] * (30 / c[0].cadence[1]
+                                 if c[0].cadence[0] == "days" else 1) for c in combo)
```

Otherwise document the heuristic as per-occurrence sacrifice. The samples do not establish an ordering derived from `financial_priorities`; the allowed/protected category fields are the explicit constraints. Sample 06 has only one candidate and cannot distinguish competing tie-breakers.

### 5. Late installment plans are ranked last, but remain eligible

**Location:** `code/planner.py:80–89,95–101`, `decide`.

The deadline is only a ranking key for installments. If every available candidate completes late, the first late candidate is still recommended as `affordable_with_plan`. The statement says a recommendation **must** complete by the deadline; ranking cannot relax that requirement.

**Impact:** **0/250 stored plans** finish late. The exhaustive logical matrix produces **70 disagreements in 672 coherent synthetic combinations**, all caused by a late installment surviving when no timely candidate exists. These are not 70 evaluation errors.

```text
MATRIX cases= 672 disagreements= {'late': 70}
```

Minimal guard, shared by all candidate types:

```diff
--- a/code/planner.py
+++ b/code/planner.py
@@
-    plans.sort(key=rank)
+    plans = [pl for pl in plans if pl.payments[-1][0] <= deadline
+             and st.is_safe(pl.payments, pl.change_map)]
+    plans.sort(key=rank)
```

The replay also checks partial and wait candidates, which currently skip direct verification. For an installments-only user whose only safe offer ends late, use `not_recommended`; the fallback status depends on the independent earliest-full date. A deadline before the request date similarly disqualifies payment today. No evaluation request has that past-deadline condition.

### 6. Capacity functions disagree with the simulator at the beginning of the forecast

**Location:** `code/engine.py:384–398`, `safe_today` and `earliest_full`.

There are two boundary defects:

- `earliest_full` checks only the suffix starting at payment time. It can return a date even when the balance already breached the minimum earlier in the request-anchored forecast. A later payment cannot repair that earlier breach.
- `safe_today` subtracts the proposed payment from today's pre-credit low, even though `path` applies today's payment **after** today's credits. Consequently `safe_today=0` can coexist with a safe full payment today and `earliest_full=today`.

**Impact:** No current output fails the corresponding capacity/replay checks. These are synthetic boundary defects, relevant if requests or input timing change. They do not explain the current sample 17 miss.

Checker evidence:

```text
PREFIX earliest= 2026-01-03 is_safe= False
SAME_DAY_CREDIT safe_today= 0.0 full_100_safe= True earliest= 2026-01-01
```

The first case starts with 100/minimum 50, incurs a 75 debit on day 2, and receives 200 on day 3. The second starts exactly at minimum 50 and receives 200 today.

Minimal consistent request-anchored checks:

```diff
--- a/code/engine.py
+++ b/code/engine.py
@@
     def safe_today(self, changes=None):
-        low = min(b for _, b, _ in self.path(changes))
+        p = self.path(changes)
+        if any(b < self.min_bal - 1e-9 for _, b, _ in p):
+            return 0.0
+        low = min([p[0][2]] + [b for _, b, _ in p[1:]])
         return max(0.0, low - self.min_bal)
@@
     def earliest_full(self, amount, changes=None):
         p = self.path(changes)
+        if any(b < self.min_bal - 1e-9 for _, b, _ in p):
+            return None
```

The suffix-minimum calculation itself is appropriate for a payment after the day's recorded cash flows. It should preserve the day's pre-payment low without subtracting money that has not yet been paid.

### 7. The production validator checks installment count, not the supplied schedule

**Location:** `code/main.py:84–92`, `validate`.

An installment passes if any offered installment option has the same number of payments. Dates and amounts are not checked. Spending validation only checks `flexibility != fixed`; it does not enforce ownership, protection, permitted action/category, reduction minimum, recurrence, or mutually exclusive actions. Partial validation omits its dates, permissions and exact first payment. Row count and full replay are also absent.

**Impact:** No current structural violation was found by the independent checker. This is a validation gap, not evidence that all 63 installment outputs are invalid. The README overstates what `main.validate` proves.

Checker reproduction changes a real installment output to the same count of `2099-01-01:1` payments:

```text
VALIDATOR accepted corrupted installment dates=2099-01-01 and amounts=1
```

Minimal fix for the reproduced schedule hole, reusing existing formatting and schedule code:

```diff
--- a/code/main.py
+++ b/code/main.py
@@
-from planner import decide  # noqa: E402
+from planner import decide, option_schedule, fmt_amt  # noqa: E402
@@
-            n = len(row["payment_plan"].split("|"))
-            assert any(int(o["number_of_payments"]) == n and o["payment_method"] == "installments" for o in opts)
+            assert any(o["payment_method"] == "installments" and
+                       row["payment_plan"] == "|".join(
+                           f"{on.isoformat()}:{fmt_amt(a)}" for on, a in option_schedule(o))
+                       for o in opts)
```

This small diff fixes the demonstrated hole only. The standalone audit performs the broader requested checks. Before future submission changes, carry those checks into the validation path or run the audit against freshly generated output.

## Status/method decision matrix

Let F = safe full payment today without changes; C = safe full payment today with permitted changes; P = valid partial schedule; I = safe supplied installment option; W = safe later full payment. The table enumerates all eight acceptance subsets. Apply the capacity/deadline gates below, then the specified ranking to whichever candidates remain.

| User accepts | Candidate families |
|---|---|
| none | none |
| full | F, C, W |
| partial | P |
| installments | I |
| full + partial | F, C, W, P |
| full + installments | F, C, W, I |
| partial + installments | P, I |
| all three | F, C, W, P, I |

| Capacity / earliest date | F | P, when accepted and request permits | W, when full accepted |
|---|---|---|---|
| safe = R, earliest = today | eligible | ineligible: no strictly positive remainder | ineligible: not later |
| safe = 0, earliest later and by deadline | no | no | eligible |
| 0 < safe < R, earliest later and by deadline | no | eligible if complete schedule passes safety | eligible |
| safe < R, earliest after deadline | no | no | no |
| safe < R, no earliest in window | no | no | no |

C independently requires a feasible set of at most three permitted changes and today's date no later than the deadline. I independently requires acceptance, a supplied option within the user's limit, a non-past start, a safe full schedule, and completion by the deadline. An infeasible or late I contributes no candidate, even if it is the only accepted method. Changes may also accompany I.

For coherent capacity semantics, safe = R implies earliest = today. The other five combinations in the raw 3-capacity-by-4-earliest Cartesian product are inconsistent; the same-day bug in finding 6 can create two of them in the current implementation. The checker exercises the seven coherent states across all eight subsets, two partial-permission states, three installment states, and two full-payment-change feasibility states: **672 cases**. It uses a fixed-cost synthetic offer to isolate eligibility rather than forecast estimation.

After excluding late/unsafe candidates, rank: no changes, lower total cost, earlier start, fewer payments, lowest option ID. This explains why a timely no-change W can beat a today-with-cuts C. A partial schedule can beat W because they cost the same and partial starts earlier.

| Selected candidate | Status | Method |
|---|---|---|
| F | affordable_now | full_payment |
| C | affordable_with_plan | full_payment |
| P | affordable_with_plan | partial_payment |
| I, including installments-only user with earliest=today | affordable_with_plan | installments |
| W | affordable_later | wait |
| none, earliest strictly later (including after deadline) | affordable_later | not_recommended |
| none, earliest absent | not_affordable | not_recommended |
| none, earliest=today, full not accepted | not_affordable | not_recommended |

The final line is the current reasonable fallback, but the statement does not explicitly settle its status wording: financial capacity exists, yet no accepted complete plan exists, and `affordable_later` would incorrectly say “later.” Keep it rather than inventing an unsupported mapping. Earliest remains today. Do not erase capacity dates because of method preferences or deadlines.

## Earliest-date semantics and calibration evidence

The strongest textual reading is **a fixed 90-day forecast anchored at request_date**. Each hypothetical full payment is inserted into that forecast. For a plan extending beyond the window, continuing through the last payment is a sensible extra check already implemented. The statement does not require a fresh 90 days after every candidate payment date.

The checker also tested that rolling interpretation, searching payment dates within the original 90 days and replaying through payment_date + 90:

```text
REQUEST_04 earliest_fixed90= 2024-06-15 earliest_rolling90= 2024-06-15 deadline= 2024-06-19
EARLIEST_SAMPLE_FIXED90_VS_ROLLING90 18 17 changed= [('request_23', '2025-07-15', '')]
```

Request_04 does **not** discriminate between the two windows. Both permit its June 15 payday before June 19. Across all 25, rolling loses one more earliest-date match, request_23. Payday-aligned dates alone do not establish either window: both naturally improve capacity when income lands.

The following sweep holds all other heuristics fixed. Counts are out of 25:

| Horizon / order | Status | Method | Plan | Earliest | Changes | Amount within 5% |
|---|---:|---:|---:|---:|---:|---:|
| 84 / hybrid | 22 | 23 | 22 | 22 | 22 | 16 |
| 84 / net | 22 | 23 | 22 | 22 | 22 | 14 |
| 84 / debit_first | 22 | 23 | 22 | 21 | 22 | 15 |
| 90 / hybrid | 19 | 20 | 19 | 18 | 21 | 14 |
| 90 / net | 19 | 20 | 19 | 18 | 21 | 12 |
| 90 / debit_first | 19 | 20 | 19 | 17 | 21 | 13 |

Equivalent isolated commands in PowerShell:

```powershell
$env:HORIZON = '90'
$env:ORDER = 'hybrid'
python code/main.py --samples
$env:ORDER = 'net'
python code/main.py --samples
Remove-Item Env:HORIZON
Remove-Item Env:ORDER
```

The audit runs these parameter combinations in memory and resets them within its process. The default CLI was independently run and returned status 22/25, method 23/25, plan/earliest/changes 22/25, amount within 5% 16/25, median relative amount error 0.020.

Hybrid versus net is not settled by the statement's date-only inputs. Hybrid gives lower amount error on samples 04/13/18/23; net is much closer on 25. The existing `net` implementation is daily netting with an opening-balance check, not proof of actual bank settlement order. A universally debit-first interpretation is more conservative but also changes outputs. Keep hybrid as a documented timing assumption if optimizing to current evidence; there is no evidence here for a simultaneous wholesale switch to 90/net.

The request-day exemption for day-cadence expenses is likewise an assumption, not an explicit rule. It needs evidence that today's expense is already represented in available balance. It should not be described as a known organizer rule.

## Message effects: what is established and what remains uncertain

**Dated salary increases:** The parser conflates a rate-effective date with a credit date. In these inputs, all 47 evaluation dated-salary effects fall on the 15th; the eight evaluation increases align with normal payroll timing. No current off-payday increase is demonstrated. For a future message effective on another day, retain the regular payday and apply the new rate from that date; do not create a deposit merely because a rate changed. First-job/explicit credit-date messages correctly use the supplied credit date. No speculative date rewrite is proposed for these rows.

**Temporary and next reduced pay:** There are nine evaluation messages of each template. Their wording confirms the next cycle, not necessarily an indefinite rate. The code changes every future payment. However, automatically restoring an older larger salary would also invent unconfirmed income. Sample_08 cannot settle permanence: its message says EUR 1,422.85, its four older salaries are also EUR 1,422.85, and only its last historical salary is EUR 782.57. Keeping the announced amount beyond the first payroll is an assumption; do not use this sample as proof that any temporary reduction lasts forever.

**Salary resumes plus childcare:** Seven evaluation users and sample_14 have this message. No amount is supplied in the message, and the inspected childcare cases have no matching childcare event to price. Ignoring an unknown amount avoids inventing an expense, but a categorical “minimum protected” assurance hides that missing commitment. Prefer explaining that a new childcare cost is unpriced. There is no defensible numeric deduction available from this evidence alone, nor a specified unknown-cost status; guessing an amount or rejecting every such request is unsupported.

**Remaining household salary:** Correct on all seven evaluation cases. Each leaves one Primary household salary series, monthly on the 15th, at the confirmed remaining total. The stale other series is removed. Evidence: `MESSAGE_TIMING ... 'household_total': 7` with assertions on the resulting cadence and series count.

**Confirmed invoice and gig payout:** A confirmed invoice correctly adds one dated one-off credit. Clearing *all* income is overbroad if a user also has an unrelated salary, and `related_event_id` is currently not used to scope effects. On these evaluation inputs, tracing before `_apply_effects` shows no eligible regular salary being cleared:

```text
INVOICE_GIG_CLEAR_REGULAR_SALARY current_250= []
```

For that future mixed-income case, the smallest defensive scope change would preserve salary series and remove only irregular series:

```diff
@@
             elif kind == "no_recurring_income":
-                self.income_series = []
+                self.income_series = [s for s in self.income_series
+                                      if not IRREGULAR_INCOME.search(s.description)]
```

This has no demonstrated current scoring benefit. The earlier income filter already excludes freelance/gig series.

**Moved payday:** The current override permanently moves later payrolls too. The message explicitly replaces the next payroll date; ongoing cadence is less explicit. Sample_07's expected October 23 date supports the current choice for that template. It is not a newly established failure.

**Sample_11:** The message explicitly confirms IDR 38,760,000; settled base history is IDR 23,256,000. The conflict rule supports using the newer explicit payroll amount. Overriding it with history simply to match a sample is not justified. In addition, the predicted no-change May 15 wait completes before its June 12 deadline, so it properly outranks a today-with-cuts plan under the written ranking. A spending tie-breaker alone cannot reconcile the sample's full recommendation.

**Coverage is not semantic validation:** `python code/messages.py` prints `unparsed: 0` for 215 messages. That proves template coverage, not that every amount, lifecycle, date scope, or conflict has been interpreted correctly.

## Explanation usefulness and submission checks

The numeric/date/currency checker finds no stray monetary values or dates in the 250 explanations. Every explanation names the minimum. However, the templates mostly restate outputs. Improve them using facts already present in `State`, without adding a model call:

- For installments, include the final payment date and total payable/financing cost, not just count, amount and start.
- For a wait, identify the confirming income event when it exists; do not label every improvement a salary.
- For a safety constraint, name the lowest projected balance and its date. Only name a particular bill if its actual flow explains that low; the low can result from several obligations.
- For unpriced childcare, explicitly state that the assurance is conditional on that missing cost.

A grounded request_144 alternative is: “Pay USD 233.34 on 15 July 2026, after the confirmed USD 864 salary. This avoids spending cuts and preserves the USD 600 minimum under the forecast.” Its numerical assurance still needs the chosen horizon to match the specification.

Verified contract items:

| Check | Result / evidence |
|---|---|
| Terminal execution | `python code/main.py --samples` and `python code/messages.py` exit 0 |
| Full prediction reproducibility | Fresh in-memory execution equals all 250 stored rows, every column |
| Exact columns, IDs and count | Pass; one output per requested ID |
| Partial contract | All 11 schedules have exactly two payments, correct dates/amounts, permissions and deadline; see separate rounding finding |
| Installment contract | All 63 outputs match a supplied option's exact dates, amounts and count, with totals within a cent |
| Installment limits | Pass under the implementation's `round(n * frequency_days / 30)` convention; the spec gives no explicit conversion formula, so this is not independent proof of that convention |
| Spending changes | All emitted changes are owned, recurring, flexible, non-protected, permitted by action/category, no conflicts, at most three; reductions equal the supplied minimum |
| Status/date consistency | affordable_now implies request-day earliest; no affordable_later has empty earliest |
| Explanations | Numeric/date/currency checks pass; see findings 1 and 3 and the unknown-childcare caveat |
| FX | All 140 foreign events have the exact stated-direction settlement-date rate; fallback does not substitute for a missing actual-event rate |
| Blank amounts | All 16 linked image events have cached amounts; no missing-image blank is silently used on current data |
| Input boundaries | Code loads participant CSVs from `dataset/`; no organizer-only prediction input or hardcoded decision labels found |
| Cache provenance | Manual image transcription is explicitly labeled; it is evidence extraction, not hardcoded request decisions |
| Secrets | Provider clients use environment variables; the optional `.env` loader preserves already-set values; no secrets are included in the reviewed package |
| Package | All 16 ZIP members byte-match the working files; setup/run README and `code/evaluation/usage_report.md` are present |
| Usage report | Reports the cached final 250-request run, zero live calls/tokens/cost, and manual-transcription provenance; no live-model execution is claimed as verified by this review |

The ZIP retains a `code/` directory, so `evaluation/usage_report.md` is relative to the solution directory (`code/evaluation/usage_report.md` in the archive), consistent with the repository's run layout. The specification does not explicitly require flattening the archive; this is not a proven packaging failure.

Previously fixed behavior remains present: category-first grouping, stale-income exclusion, on-pattern cadence anchor, image exclusion from recurrence, foreign debit conversion before grouping, long-plan replay, wait deadline gate, and dated first-salary handling. No new violation of those fixes was established on the supplied rows. Broader lifecycle handling and unseen template behavior remain outside what sample agreement can prove.
