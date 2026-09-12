"""Read-only second-opinion checks; run: python review_second_opinion_check.py.

Prints observed violations rather than changing predictions or production code.
Synthetic cases reproduce reviewed defects; they are not evaluation labels.
"""
import ast
import csv
import itertools
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "code"))
for key in ("ORDER", "EXCL_TODAY", "EST", "SCALE", "MIN_GAP", "IGNORE_MSG_SALARY"):
    os.environ.pop(key, None)
os.environ["HORIZON"] = "84"
import engine
import planner
from data import Data, d
from messages import parse_message

FIELDS = ["affordability_status", "recommended_payment_method", "payment_plan",
          "earliest_date_for_full_payment", "spending_changes_needed", "amount_safe_to_pay"]
DATA = Data()
CACHE = json.loads((ROOT / "code/image_cache.json").read_text(encoding="utf-8"))
IMAGES = {eid: CACHE[im["image_id"]]["amount"] for eid, im in DATA.images_by_event.items()}


def state(req):
    return engine.State(DATA, req, IMAGES)


def rows(requests):
    return [planner.decide(state(r), DATA) for r in requests]


def payments(row):
    if row["payment_plan"] == "none":
        return []
    return [(d(t), Decimal(a)) for t, a in
            (x.split(":") for x in row["payment_plan"].split("|"))]


def audit(base):
    with (ROOT / "output.csv").open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        stored = list(reader)
        assert reader.fieldnames == ["request_id", "amount_safe_to_pay", "affordability_status",
                                     "recommended_payment_method", "payment_plan",
                                     "earliest_date_for_full_payment", "spending_changes_needed",
                                     "decision_explanation"]
    assert stored == base, "Fresh predictions differ from output.csv"
    assert Counter(r["request_id"] for r in stored) == Counter(r["request_id"] for r in DATA.requests)
    problems = defaultdict(list)
    unsafe = defaultdict(list)
    wait_with_cuts = []
    for req, row in zip(DATA.requests, stored):
        rid = req["request_id"]
        st = state(req)
        profile = st.p
        R, safe = Decimal(req["requested_amount"]), Decimal(row["amount_safe_to_pay"])
        end, earliest = d(req["desired_completion_date"]), d(row["earliest_date_for_full_payment"])
        method, status = row["recommended_payment_method"], row["affordability_status"]
        accepts = set(profile["payment_methods_user_will_consider"].split("|"))
        pay = payments(row)

        def check(ok, kind):
            if not ok:
                problems[kind].append(rid)

        check(0 <= safe <= R, "safe_bounds")
        check(pay == sorted(pay), "chronological")
        check(all(a > 0 and t >= st.rd for t, a in pay), "positive_future_payments")
        check(not pay or pay[-1][0] <= end, "deadline")
        check(not earliest or st.rd <= earliest <= st.end, "earliest_window")
        check(status != "affordable_now" or (earliest == st.rd and safe == R and
              method == "full_payment" and "full_payment" in accepts), "now_implication")
        check(earliest is not None or status != "affordable_later", "empty_earliest_later")
        if method == "not_recommended":
            check(not pay and row["spending_changes_needed"] == "none", "fallback_plan")
        elif method == "full_payment":
            check(method in accepts and pay == [(st.rd, R)], "full_plan")
        elif method == "wait":
            check("full_payment" in accepts and earliest and earliest > st.rd and
                  pay == [(earliest, R)], "wait_plan")
            if st.cheapest_changes([(st.rd, float(R))]):
                wait_with_cuts.append(rid)
        elif method == "partial_payment":
            check(method in accepts and req["allows_partial_payment"].lower() == "true" and
                  0 < safe < R and earliest is not None and earliest <= end and
                  pay == [(st.rd, safe), (earliest, R - safe)] and
                  status == "affordable_with_plan", "partial_contract")
        elif method == "installments":
            matches = []
            for opt in DATA.options_by_request.get(rid, []):
                if opt["payment_method"] != method:
                    continue
                n, freq = int(opt["number_of_payments"]), int(opt["payment_frequency_days"] or 0)
                sched = [(d(opt["first_payment_date"]) + timedelta(days=i * freq),
                          Decimal(opt["payment_amount"])) for i in range(n)]
                if pay == sched:
                    matches.append(opt)
            check(method in accepts and status == "affordable_with_plan" and bool(matches),
                  "installment_exact_option")
            # The spec does not define days-to-months conversion. Test the implementation's
            # convention separately from exact schedule matching; report that limitation.
            limit = int(profile["max_installment_months"] or 0)
            check(bool(matches) and all(limit and max(1, round(int(o["number_of_payments"]) *
                  int(o["payment_frequency_days"] or 0) / 30)) <= limit for o in matches),
                  "installment_month_limit_current_convention")
            check(all(abs(sum(a for _, a in pay) - Decimal(o["total_payable_amount"])) <=
                  Decimal("0.01") for o in matches), "installment_total")
        else:
            check(False, "unknown_method")

        changes = {}
        allowed_money = {R, safe, Decimal(str(st.min_bal))} | {a for _, a in pay}
        raw = [] if row["spending_changes_needed"] == "none" else row["spending_changes_needed"].split("|")
        check(len(raw) <= 3, "change_count")
        for text in raw:
            part = text.split(":")
            kind, eid = part[:2]
            ev = DATA.event_by_id[eid]
            cat = ev["category"]
            check(eid not in changes, "change_duplicate_or_conflict")
            check(ev["user_id"] == req["user_id"] and ev["direction"] == "debit" and
                  cat not in profile["expense_categories_to_protect"].split("|") and
                  any(s.ref_event == eid and len(s.events) >= 3 for s in st.debit_series),
                  "change_recurring_nonprotected_owned")
            if kind == "stop":
                check(ev["flexibility"] in ("stoppable", "reducible_or_stoppable") and
                      cat in profile["expense_categories_user_is_willing_to_stop"].split("|"),
                      "stop_permission")
                changes[eid] = "stop"
            else:
                value = Decimal(part[2])
                check(kind == "reduce_to" and ev["flexibility"] in ("reducible", "reducible_or_stoppable") and
                      cat in profile["expense_categories_user_is_willing_to_reduce"].split("|") and
                      value == Decimal(ev["minimum_allowed_amount"]), "reduce_permission_and_minimum")
                changes[eid] = float(value)
                allowed_money.add(value)
        explanation = row["decision_explanation"]
        mentions = re.findall(r"\b(INR|ZAR|IDR|USD|EUR) ([\d,]+(?:\.\d+)?)", explanation)
        check(bool(mentions) and all(cur == st.home and Decimal(a.replace(",", "")) in allowed_money
                                    for cur, a in mentions), "explanation_money_currency")
        mentioned_dates = [datetime.strptime(x, "%d %B %Y").date() for x in
                           re.findall(r"\b\d{1,2} [A-Z][a-z]+ \d{4}\b", explanation)]
        check(all(x in {end, earliest, st.rd} | {t for t, _ in pay} for x in mentioned_dates),
              "explanation_dates")
        check(any(Decimal(a.replace(",", "")) == Decimal(str(st.min_bal)) for _, a in mentions),
              "explanation_minimum_present")
        for horizon in (84, 90):
            st.end = st.rd + timedelta(days=horizon)
            if pay:
                low = min(st.path(changes, [(t, float(a)) for t, a in pay]), key=lambda x: x[1])
                deficit = st.min_bal - low[1]
                if deficit > 1e-9:
                    unsafe[horizon].append((rid, low[0].isoformat(), round(deficit, 8)))
        st.end = st.rd + timedelta(days=84)
        check(not earliest or st.is_safe([(earliest, float(R))]), "earliest_full_replay")
    print("AUDIT reproduced=250/250 structural_or_numeric_explanation_violations=", dict(problems))
    print("SAFETY_84_STRICT", unsafe[84])
    print("SAFETY_90_MATERIAL", [x for x in unsafe[90] if x[2] > .01])
    print("SAFETY_90_COUNTS strict=", len(unsafe[90]), "material=", sum(x[2] > .01 for x in unsafe[90]))
    print("WAIT_WITH_SAFE_EARLIER_CUTS", wait_with_cuts)
    assert len(DATA.messages) == 215 and all(parse_message(m["message_text"])[0][0] != "unparsed" for m in DATA.messages)
    print("MESSAGES parsed=215/215; cached_images=", len(IMAGES))
    with zipfile.ZipFile(ROOT / "code.zip") as z:
        mismatch = [n for n in z.namelist() if not (ROOT / n).is_file() or z.read(n) != (ROOT / n).read_bytes()]
        assert not mismatch
        assert "code/evaluation/usage_report.md" in z.namelist()
        print("PACKAGE identical_members=", len(z.namelist()), "usage_report=present")
    # Compile just the existing validator, avoiding main.py's .env side effect.
    tree = ast.parse((ROOT / "code/main.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "validate")
    scope = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "main.validate", "exec"), scope)
    original = next(r for r in stored if r["recommended_payment_method"] == "installments")
    bad = dict(original)
    bad["payment_plan"] = "|".join("2099-01-01:1" for _ in payments(original))
    scope["validate"]([bad], DATA.requests, DATA)
    print("VALIDATOR accepted corrupted installment dates=2099-01-01 and amounts=1")


def sweep(base):
    for horizon, order in itertools.product((84, 90), ("hybrid", "net", "debit_first")):
        engine.HORIZON = horizon
        os.environ["ORDER"] = order
        predicted = rows(DATA.samples)
        hits = [sum(a[k] == b[k] for a, b in zip(predicted, DATA.samples)) for k in FIELDS[:5]]
        amount_hits = sum(abs(float(a["amount_safe_to_pay"]) - float(b["amount_safe_to_pay"])) <=
                          max(1e-8, .05 * float(b["amount_safe_to_pay"])) for a, b in zip(predicted, DATA.samples))
        result = rows(DATA.requests)
        changed = [sum(a[k] != b[k] for a, b in zip(result, base)) for k in FIELDS]
        print("SWEEP", horizon, order, "sample(status,method,plan,earliest,changes,amount5%)=",
              hits + [amount_hits], "250_changed(status,method,plan,earliest,changes,amount)=", changed)
    engine.HORIZON = 84
    os.environ["ORDER"] = "hybrid"


def change_search(base):
    source = (ROOT / "code/engine.py").read_text(encoding="utf-8")
    needle = "            if best:\n                break\n"
    assert source.count(needle) == 1
    scope = dict(engine.__dict__)
    exec(compile(source.replace(needle, ""), "engine-without-break", "exec"), scope)
    original = engine.State.cheapest_changes
    alternative = scope["State"].cheapest_changes
    engine.State.cheapest_changes = alternative
    try:
        revised = rows(DATA.requests)
    finally:
        engine.State.cheapest_changes = original
    print("GLOBAL_SAVINGS current_250_changed=", sum(a != b for a, b in zip(base, revised)))
    normalized = source.replace(needle, "").replace(
        "saving = sum(c[3] for c in combo)",
        'saving = sum(c[3] * (30 / c[0].cadence[1] if c[0].cadence[0] == "days" else 1) for c in combo)')
    monthly_scope = dict(engine.__dict__)
    exec(compile(normalized, "engine-monthly-savings", "exec"), monthly_scope)
    monthly = monthly_scope["State"].cheapest_changes
    engine.State.cheapest_changes = monthly
    try:
        revised = rows(DATA.requests)
    finally:
        engine.State.cheapest_changes = original
    print("MONTHLY_SAVINGS changed=", [(a["request_id"], a["spending_changes_needed"],
          b["spending_changes_needed"]) for a, b in zip(base, revised) if a != b])
    for rid in ("request_06", "request_11", "request_21"):
        req = next(r for r in DATA.samples if r["request_id"] == rid)
        st = state(req)
        st.balance += float(req["amount_safe_to_pay"]) - st.safe_today()
        pay = [(st.rd, float(req["requested_amount"]))]
        a, b = original(st, pay), alternative(st, pay)
        print("ALIGNED_HEADROOM", rid, "current=", a[0] if a else None,
              "without_break=", b[0] if b else None, "GT=", req["spending_changes_needed"])
        normalized_best = monthly(st, pay)
        print("ALIGNED_MONTHLY", rid, normalized_best[0] if normalized_best else None)


def matrix():
    methods = ("full_payment", "partial_payment", "installments")
    rd = date(2026, 1, 1)
    counter, mismatches = 0, Counter()
    # Consistent capacity states: full today implies E=today; otherwise E=later/absent.
    capacity = [(100, 0)] + list(itertools.product((0, 50), (None, 5, 20)))
    for mask, (safe, offset), partial, installment, cuts in itertools.product(
            range(8), capacity, (False, True), ("none", "timely", "late"), (False, True)):
        accepts = {m for i, m in enumerate(methods) if mask & (1 << i)}
        earliest = None if offset is None else rd + timedelta(days=offset)
        req = dict(request_id="synthetic", requested_amount="100", request_date=rd.isoformat(),
                   desired_completion_date=(rd + timedelta(days=10)).isoformat(),
                   allows_partial_payment=str(partial))
        series = SimpleNamespace(ref_event="synthetic_event", description="subscription")
        change = [(series, "stop", None, 1)]
        st = SimpleNamespace(req=req, rd=rd, home="USD", min_bal=50,
                             p=dict(payment_methods_user_will_consider="|".join(sorted(accepts)),
                                    max_installment_months="24"),
                             safe_today=lambda: safe, earliest_full=lambda _: earliest,
                             is_safe=lambda pay: safe == 100 if len(pay) == 1 else True,
                             cheapest_changes=lambda pay: ((1,), change, {}) if cuts else None)
        opts = [] if installment == "none" else [dict(payment_method="installments", number_of_payments="2",
                payment_frequency_days="2" if installment == "timely" else "20", payment_amount="55",
                first_payment_date=rd.isoformat(), total_payable_amount="110", payment_option_id="o1")]
        result = planner.decide(st, SimpleNamespace(options_by_request={"synthetic": opts}))
        # Reference eligibility excludes late plans before applying the specified ranking.
        candidates = []
        if "full_payment" in accepts and (safe == 100 or cuts):
            candidates.append(((safe != 100, 100, rd, 1, ""),
                               ("affordable_now" if safe == 100 else "affordable_with_plan", "full_payment")))
        if "partial_payment" in accepts and partial and 0 < safe < 100 and offset == 5:
            candidates.append(((False, 100, rd, 2, ""), ("affordable_with_plan", "partial_payment")))
        if "installments" in accepts and installment == "timely":
            candidates.append(((False, 110, rd, 2, "o1"), ("affordable_with_plan", "installments")))
        if "full_payment" in accepts and offset == 5:
            candidates.append(((False, 100, earliest, 1, ""), ("affordable_later", "wait")))
        expected = min(candidates)[1] if candidates else (
            "affordable_later" if offset in (5, 20) else "not_affordable", "not_recommended")
        actual = result["affordability_status"], result["recommended_payment_method"]
        counter += 1
        if actual != expected:
            mismatches[installment] += 1
    print("MATRIX cases=", counter, "disagreements=", dict(mismatches))


def precision_proposal(base):
    source = (ROOT / "code/planner.py").read_text(encoding="utf-8").replace(
        "from datetime import timedelta", "from datetime import timedelta\nfrom decimal import Decimal, ROUND_DOWN").replace(
        "safe = min(R, st.safe_today())",
        'safe = float(Decimal(str(min(R, st.safe_today()))).quantize(Decimal("0.01"), rounding=ROUND_DOWN))')
    scope = dict(planner.__dict__)
    exec(compile(source, "planner-round-down", "exec"), scope)
    changed, unsafe = 0, []
    for req, old in zip(DATA.requests, base):
        st = state(req)
        row = scope["decide"](st, DATA)
        changed += old["amount_safe_to_pay"] != row["amount_safe_to_pay"]
        if row["recommended_payment_method"] == "partial_payment":
            if not st.is_safe([(t, float(a)) for t, a in payments(row)]):
                unsafe.append(req["request_id"])
    assert not unsafe
    print("ROUND_DOWN proposal amount_columns_changed=", changed, "unsafe_partial_plans=", unsafe)


def synthetic_safety():
    rd = date(2026, 1, 1)
    st = engine.State.__new__(engine.State)
    st.rd, st.end, st.balance, st.min_bal = rd, rd + timedelta(days=3), 100, 50
    st.debit_series, st.income_series = [], []
    st.one_offs = [(rd + timedelta(days=1), -75, "bill", "bill"),
                  (rd + timedelta(days=2), 200, "salary", "salary")]
    earliest = st.earliest_full(100)
    print("PREFIX earliest=", earliest, "is_safe=", st.is_safe([(earliest, 100)]))
    assert earliest == rd + timedelta(days=2) and not st.is_safe([(earliest, 100)])
    st.balance = 50
    st.one_offs = [(rd, 200, "salary", "salary")]
    print("SAME_DAY_CREDIT safe_today=", st.safe_today(), "full_100_safe=", st.is_safe([(rd, 100)]),
          "earliest=", st.earliest_full(100))
    assert st.safe_today() == 0 and st.is_safe([(rd, 100)])


def evidence_semantics():
    engine.HORIZON = 90
    fixed_hits = rolling_hits = 0
    changed = []
    for req in DATA.samples:
        st = state(req)
        amount = float(req["requested_amount"])
        fixed = st.earliest_full(amount)
        rolling = None
        for i in range(91):
            on = st.rd + timedelta(days=i)
            st.end = on + timedelta(days=90)
            if st.is_safe([(on, amount)]):
                rolling = on
                break
        fixed_text, rolling_text = str(fixed) if fixed else "", str(rolling) if rolling else ""
        fixed_hits += fixed_text == req["earliest_date_for_full_payment"]
        rolling_hits += rolling_text == req["earliest_date_for_full_payment"]
        if fixed != rolling:
            changed.append((req["request_id"], fixed_text, rolling_text))
        if req["request_id"] == "request_04":
            print("REQUEST_04 earliest_fixed90=", fixed, "earliest_rolling90=", rolling,
                  "deadline=", req["desired_completion_date"])
    print("EARLIEST_SAMPLE_FIXED90_VS_ROLLING90", fixed_hits, rolling_hits, "changed=", changed)
    engine.HORIZON = 84
    original = engine.State._apply_effects
    clearing = []

    def trace(self):
        if self.income_series and any(e[2][0] == "no_recurring_income" for e in self.effects):
            clearing.append(self.req["request_id"])
        original(self)

    engine.State._apply_effects = trace
    try:
        for req in DATA.requests:
            state(req)
    finally:
        engine.State._apply_effects = original
    print("INVOICE_GIG_CLEAR_REGULAR_SALARY current_250=", clearing)
    counts = Counter()
    for req in DATA.requests:
        st = state(req)
        for mid, _, effect in st.effects:
            if effect[0] == "income_only":
                counts["household_total"] += 1
                assert len(st.income_series) == 1 and st.income_series[0].cadence == ("monthly", 15)
            if effect[0] == "salary_amount" and effect[-1] is not None:
                assert effect[-1].day == 15
                counts["dated_salary_day15"] += 1
    print("MESSAGE_TIMING", dict(counts))
    foreign = [e for e in DATA.events if e["currency"] != DATA.profiles[e["user_id"]]["home_currency"]]
    assert all((e["settlement_date"], e["currency"], DATA.profiles[e["user_id"]]["home_currency"])
               in DATA.rates for e in foreign)
    print("FX foreign_events_exact_direct_rate=", len(foreign))


if __name__ == "__main__":
    base = rows(DATA.requests)
    audit(base)
    sweep(base)
    change_search(base)
    matrix()
    precision_proposal(base)
    synthetic_safety()
    evidence_semantics()
