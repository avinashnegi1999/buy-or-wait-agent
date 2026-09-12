# Changes made — 13 September 2026

The work was a second-opinion review. No proposed fixes have been applied to the solution.

## Files added

| File | What it contains |
|---|---|
| `SECOND_OPINION_REVIEW.md` | Seven findings, affected-row counts, evidence, proposed minimal diffs, status/method matrix, message analysis, and submission checks. |
| `review_second_opinion_check.py` | Runnable audit that reproduces all 250 predictions, checks payment schedules and spending permissions, compares forecast settings, and exercises 672 synthetic eligibility cases. |
| `CHANGES_MADE.md` | This record of changes actually made. |

## File updated

- `log.txt`: added the session and conversation records required by `AGENTS.md`.

## Verification performed

```powershell
python review_second_opinion_check.py
python code/main.py --samples
python code/messages.py
```

- All commands completed successfully. The audit reports known defects; a successful exit does not mean there are no findings.
- Reproduced all 250 stored output rows exactly.
- Confirmed all 16 ZIP members match their working files.
- Confirmed all 215 messages parse and all 16 image amounts are cached.
- Compared 84-day and 90-day horizons under three payment-order assumptions.
- Ran 672 synthetic status/method combinations.
- Tested rounding and spending-selection alternatives in memory only.

## Fixes proposed, not applied

1. Use the required 90-day forecast instead of the default 84 days. The longer replay exposes 21 material minimum-balance breaches, but lowers public-sample agreement.
2. Round safe payment capacity downward before constructing partial payments. Five current partial plans exceed the computed capacity by less than half a cent.
3. Clarify the wait explanation when an earlier payment would be safe with spending changes; demonstrated on `request_144`.
4. Correct the spending-change search's early exit and consider cadence-normalized savings. This is partly a policy choice because the specification does not prescribe that secondary objective.
5. Reject plans that complete after the deadline, rather than merely ranking them last. Demonstrated in synthetic cases; no current output finishes late.
6. Make `safe_today` and `earliest_full` consistent with the simulator's timing and whole-period safety checks. Demonstrated in synthetic cases.
7. Strengthen production validation to check exact installment dates and amounts, plus the other output constraints.

The review also documents message ambiguities and a defensive proposal for preserving unrelated salary income when processing invoice/gig messages.

## Submission files preserved

- No production files under `code/` were edited.
- `output.csv` was not regenerated or changed.
- `code.zip` was not rebuilt or changed.
- No dataset files were changed.
- No commits, pushes, or submissions were made.

See `SECOND_OPINION_REVIEW.md` for the evidence and proposed diffs. Run `python review_second_opinion_check.py` to reproduce the audit.
