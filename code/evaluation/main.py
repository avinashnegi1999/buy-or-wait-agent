"""Evaluation workflow: score predictions against the solved sample requests.

Run:  python code/main.py --samples
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def score(rows, samples):
    gt = {r["request_id"]: r for r in samples}
    n = len(rows)
    hits = {"status": 0, "method": 0, "plan": 0, "earliest": 0, "changes": 0, "amount_5pct": 0, "amount_1pct": 0}
    rel = []
    for row in rows:
        g = gt[row["request_id"]]
        hits["status"] += row["affordability_status"] == g["affordability_status"]
        hits["method"] += row["recommended_payment_method"] == g["recommended_payment_method"]
        hits["plan"] += row["payment_plan"] == g["payment_plan"]
        hits["earliest"] += row["earliest_date_for_full_payment"] == g["earliest_date_for_full_payment"]
        hits["changes"] += row["spending_changes_needed"] == g["spending_changes_needed"]
        a, b = float(row["amount_safe_to_pay"]), float(g["amount_safe_to_pay"])
        e = abs(a - b) / b if b else (0.0 if a == 0 else 1.0)
        rel.append(e)
        hits["amount_5pct"] += e <= 0.05
        hits["amount_1pct"] += e <= 0.01
        flag = "" if (row["affordability_status"] == g["affordability_status"] and row["recommended_payment_method"] == g["recommended_payment_method"]) else "  <-- MISMATCH"
        print(f"{row['request_id']}: {row['affordability_status']:20} {row['recommended_payment_method']:16} "
              f"safe {a:>14.2f} (gt {b:>14.2f}) earliest {row['earliest_date_for_full_payment'] or '-':10} "
              f"(gt {g['earliest_date_for_full_payment'] or '-':10}) changes {row['spending_changes_needed']} (gt {g['spending_changes_needed']}){flag}")
    print()
    for k, v in hits.items():
        print(f"{k:12} {v}/{n}")
    rel.sort()
    print(f"amount rel err: mean {sum(rel) / n:.3f} median {rel[n // 2]:.3f}")


if __name__ == "__main__":
    from main import Data, Usage, extract_amounts, run
    data = Data()
    u = Usage()
    rows = run(data.samples, data, u, extract_amounts(data, u))
    score(rows, data.samples)
