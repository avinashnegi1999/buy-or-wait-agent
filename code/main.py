"""Buy or Wait? — entry point.

Usage:  python code/main.py            -> writes output.csv in the repo root
        python code/main.py --samples  -> also scores against dataset/sample_requests.csv
"""
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import Data  # noqa: E402
from engine import State  # noqa: E402
from images import extract_amounts  # noqa: E402
from planner import decide  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv():
    """Minimal .env loader (repo root); real env vars win. Never prints values."""
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()
COLUMNS = ["request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method",
           "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation"]


class Usage:
    """Token/cost accounting for every model call made during a run."""
    PRICES = {  # USD per 1M tokens (input, output)
        "claude-sonnet-5": (3.0, 15.0), "claude-opus-5": (15.0, 75.0), "claude-haiku-4-5-20251001": (1.0, 5.0),
        "gpt-4.1-mini": (0.4, 1.6), "gpt-4o-mini": (0.15, 0.6), "gpt-4.1": (2.0, 8.0), "gpt-4o": (2.5, 10.0),
    }

    def __init__(self):
        self.calls = {}

    def record(self, model, inp, out):
        c = self.calls.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        c["calls"] += 1
        c["input_tokens"] += inp
        c["output_tokens"] += out

    def cost(self, model, c):
        pi, po = self.PRICES.get(model, (3.0, 15.0))
        return c["input_tokens"] / 1e6 * pi + c["output_tokens"] / 1e6 * po


def run(requests, data, usage, image_amounts):
    rows = []
    for r in requests:
        st = State(data, r, image_amounts)
        rows.append(decide(st, data))
    return rows


def validate(rows, requests, data):
    reqs = {r["request_id"]: r for r in requests}
    for row in rows:
        r = reqs[row["request_id"]]
        R = float(r["requested_amount"])
        s = float(row["amount_safe_to_pay"])
        assert 0 <= s <= R + 1e-6, row
        assert row["affordability_status"] in ("affordable_now", "affordable_with_plan", "affordable_later", "not_affordable")
        assert row["recommended_payment_method"] in ("full_payment", "partial_payment", "installments", "wait", "not_recommended")
        if row["affordability_status"] == "affordable_now":
            assert row["earliest_date_for_full_payment"] == r["request_date"]
        if row["recommended_payment_method"] == "partial_payment":
            parts = row["payment_plan"].split("|")
            assert len(parts) == 2 and row["affordability_status"] == "affordable_with_plan"
            assert abs(sum(float(p.split(":")[1]) for p in parts) - R) < 0.011
        if row["recommended_payment_method"] == "installments":
            opts = data.options_by_request[row["request_id"]]
            n = len(row["payment_plan"].split("|"))
            assert any(int(o["number_of_payments"]) == n and o["payment_method"] == "installments" for o in opts)
        if row["spending_changes_needed"] != "none":
            for ch in row["spending_changes_needed"].split("|"):
                eid = ch.split(":")[1]
                ev = data.event_by_id[eid]
                assert ev["flexibility"] != "fixed", ch


def main():
    t0 = time.time()
    data = Data()
    usage = Usage()
    image_amounts = extract_amounts(data, usage)
    if "--samples" in sys.argv:
        from evaluation.main import score
        rows = run(data.samples, data, usage, image_amounts)
        score(rows, data.samples)
        return
    rows = run(data.requests, data, usage, image_amounts)
    validate(rows, data.requests, data)
    out = os.path.join(ROOT, "output.csv")
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    write_usage_report(usage, len(rows), time.time() - t0, image_amounts)
    print(f"wrote {out}: {len(rows)} rows in {time.time() - t0:.1f}s; model calls: {usage.calls or 'none (cached)'}")


def write_usage_report(usage, n_requests, seconds, image_amounts):
    """evaluation/usage_report.md for the run that produced output.csv."""
    cache_path = os.path.join(ROOT, "code", "image_cache.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    models_in_cache = sorted({v.get("model", "?") for v in cache.values()})
    lines = ["# Token usage and cost report", "",
             f"Run: `python code/main.py` over {n_requests} requests in {seconds:.1f}s.", "",
             "## Architecture", "",
             "The decision engine (state reconstruction, 84-day cash-flow forecast, plan search, ranking,",
             "explanation) is fully deterministic and makes **no model calls**. Models are used for one thing:",
             "reading the amount of a blank-amount financial event from its linked image (16 images).",
             "Results are cached in `code/image_cache.json`, so a re-run is offline and reproducible;",
             "delete the cache to force re-extraction.", "",
             "## Model calls in this run", ""]
    if not usage.calls:
        lines += [f"No live model calls: all {len(image_amounts)} image amounts were served from the cache.",
                  f"Cache entries were produced by: {', '.join(models_in_cache) or 'n/a'}.", ""]
    tot_in = tot_out = tot_cost = tot_calls = 0
    lines += ["| provider | model | calls | input tokens | output tokens | est. cost (USD) |",
              "|---|---|---|---|---|---|"]
    for model, c in usage.calls.items():
        provider = "Anthropic" if model.startswith("claude") else "OpenAI"
        cost = usage.cost(model, c)
        tot_in += c["input_tokens"]; tot_out += c["output_tokens"]; tot_cost += cost; tot_calls += c["calls"]
        lines.append(f"| {provider} | {model} | {c['calls']} | {c['input_tokens']} | {c['output_tokens']} | {cost:.4f} |")
    lines.append(f"| **total** | | {tot_calls} | {tot_in} | {tot_out} | {tot_cost:.4f} |")
    lines += ["", "## Per-request averages", "",
              f"- total tokens: {tot_in + tot_out}",
              f"- average tokens per request: {(tot_in + tot_out) / n_requests:.1f}",
              f"- average model calls per request: {tot_calls / n_requests:.3f}",
              f"- estimated total cost: ${tot_cost:.4f}",
              f"- estimated cost per request: ${tot_cost / n_requests:.6f}", "",
              "Prices used (USD per 1M tokens, input/output): " +
              ", ".join(f"{m} {p[0]}/{p[1]}" for m, p in Usage.PRICES.items()) + ".", ""]
    with open(os.path.join(ROOT, "code", "evaluation", "usage_report.md"), "w", encoding="utf-8") as f:
        f.write(chr(10).join(lines))


if __name__ == "__main__":
    main()
