"""Enumerate candidate payment plans, verify them deterministically, rank per the spec."""
from datetime import timedelta

from data import d, f


def fmt_amt(x):
    x = round(x + 1e-9, 2)
    return str(int(x)) if abs(x - int(x)) < 1e-9 else f"{x:.2f}"


def fmt_num(x):
    """amount_safe_to_pay style: 2 decimals, trailing zeros stripped."""
    x = round(x + 1e-9, 2)
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s or "0"


def option_schedule(opt):
    first = d(opt["first_payment_date"])
    n = int(opt["number_of_payments"])
    amt = float(opt["payment_amount"])
    freq = opt["payment_frequency_days"]
    dates = [first]
    for _ in range(n - 1):
        dates.append(dates[-1] + timedelta(days=int(freq)))
    return [(dt, amt) for dt in dates]


class Plan:
    def __init__(self, method, payments, changes=(), option=None, total=None):
        self.method = method
        self.payments = payments  # [(date, amount)]
        self.changes = list(changes)  # [(series, kind, new_amount, saving)]
        self.option = option
        self.total = total if total is not None else sum(a for _, a in payments)

    @property
    def change_map(self):
        return {c[0].ref_event: ("stop" if c[1] == "stop" else c[2]) for c in self.changes}


def decide(st, data):
    req = st.req
    R = float(req["requested_amount"])
    deadline = d(req["desired_completion_date"])
    accepts = set(filter(None, st.p["payment_methods_user_will_consider"].split("|")))
    max_months = st.p["max_installment_months"]
    max_months = int(max_months) if max_months else 0
    allows_partial = req["allows_partial_payment"].strip().lower() == "true"

    safe = min(R, st.safe_today())
    earliest = st.earliest_full(R)
    plans = []

    # full payment today
    if "full_payment" in accepts:
        pay = [(st.rd, R)]
        if st.is_safe(pay):
            plans.append(Plan("full_payment", pay))
        else:
            best = st.cheapest_changes(pay)
            if best:
                plans.append(Plan("full_payment", pay, best[1]))

    # partial payment: safe today + remainder on earliest full date
    if "partial_payment" in accepts and allows_partial and 0 < safe < R and earliest and earliest <= deadline:
        plans.append(Plan("partial_payment", [(st.rd, safe), (earliest, R - safe)]))

    # installments matching a supplied option
    if "installments" in accepts:
        for opt in data.options_by_request.get(req["request_id"], []):
            if opt["payment_method"] != "installments":
                continue
            n = int(opt["number_of_payments"])
            freq = int(opt["payment_frequency_days"] or 0)
            months = max(1, round(n * freq / 30)) if freq else 1
            if max_months and months > max_months:
                continue
            sched = option_schedule(opt)
            if sched[0][0] < st.rd:
                continue
            total = float(opt["total_payable_amount"])
            if st.is_safe(sched):
                plans.append(Plan("installments", sched, option=opt, total=total))
            else:
                best = st.cheapest_changes(sched)
                if best:
                    plans.append(Plan("installments", sched, best[1], option=opt, total=total))

    # wait for the full amount to become safe
    if "full_payment" in accepts and earliest and st.rd < earliest <= deadline:
        plans.append(Plan("wait", [(earliest, R)]))

    def rank(pl):
        completes = pl.payments[-1][0] <= deadline
        return (not completes, bool(pl.changes), round(pl.total, 2), pl.payments[0][0], len(pl.payments),
                pl.option["payment_option_id"] if pl.option else "")

    plans.sort(key=rank)
    chosen = plans[0] if plans else None

    if chosen is None:
        # spec: affordable_later = full amount becomes safe later (independent of method preferences)
        status = "affordable_later" if earliest and earliest > st.rd else "not_affordable"
        method = "not_recommended"
    elif chosen.method == "full_payment" and not chosen.changes:
        status, method = "affordable_now", "full_payment"
    elif chosen.method == "wait":
        status, method = "affordable_later", "wait"
    else:
        status, method = "affordable_with_plan", chosen.method

    plan_str = "|".join(f"{on.isoformat()}:{fmt_amt(a)}" for on, a in chosen.payments) if chosen else "none"
    changes_str = "|".join(
        f"stop:{c[0].ref_event}" if c[1] == "stop" else f"reduce_to:{c[0].ref_event}:{fmt_amt(c[2])}"
        for c in chosen.changes) if chosen and chosen.changes else "none"
    earliest_str = earliest.isoformat() if earliest else ""
    if status == "affordable_now":
        earliest_str = st.rd.isoformat()

    return {
        "request_id": req["request_id"],
        "amount_safe_to_pay": fmt_num(safe),
        "affordability_status": status,
        "recommended_payment_method": method,
        "payment_plan": plan_str,
        "earliest_date_for_full_payment": earliest_str,
        "spending_changes_needed": changes_str,
        "decision_explanation": explain(st, chosen, status, safe, R, earliest, deadline),
    }


def money(cur, x):
    x = round(x + 1e-9, 2)
    if abs(x - int(x)) < 1e-9:
        return f"{cur} {int(x):,}"
    return f"{cur} {x:,.2f}"


def longdate(dt):
    return f"{dt.day} {dt.strftime('%B %Y')}"


def explain(st, pl, status, safe, R, earliest, deadline):
    cur = st.home
    mn = money(cur, st.min_bal)
    if pl is None:
        if earliest is not None and earliest > deadline:
            return (f"Do not make this payment by {longdate(deadline)}. The full {money(cur, R)} is only forecast to be "
                    f"safe from {longdate(earliest)}, and no earlier option keeps the {mn} minimum protected.")
        if earliest is not None:
            return (f"Do not proceed now. The full {money(cur, R)} is forecast to be safe from {longdate(earliest)}, "
                    f"but none of the payment methods you will consider completes it while keeping the {mn} minimum protected.")
        accepts_partial = "partial_payment" in st.p["payment_methods_user_will_consider"].split("|")
        allows_partial = st.req["allows_partial_payment"].strip().lower() == "true"
        if safe > 0 and accepts_partial and allows_partial:
            return (f"Do not proceed with the {money(cur, R)} request. Although {money(cur, safe)} is available today, "
                    f"the full amount cannot be completed safely within 90 days while keeping the {mn} minimum.")
        return (f"Do not make this payment by {longdate(deadline)}. None of the available options keeps the "
                f"{mn} minimum protected.")
    if pl.method == "full_payment":
        if pl.changes:
            parts = []
            for s, kind, new, _ in pl.changes:
                name = s.description.lower()
                parts.append(f"stop the {name}" if kind == "stop" else f"reduce the {name} to {money(cur, new)}")
            lead = " and ".join(parts)
            lead = lead[0].upper() + lead[1:]
            return f"{lead}, then pay {money(cur, R)} today. This leaves at least {mn} available."
        return f"Pay {money(cur, R)} today. This leaves at least {mn} available over the next 90 days."
    if pl.method == "partial_payment":
        (d1, a1), (d2, a2) = pl.payments
        return (f"Pay {money(cur, a1)} today and the remaining {money(cur, a2)} on {longdate(d2)}. "
                f"This completes the full request and keeps the {mn} minimum protected.")
    if pl.method == "installments":
        n = len(pl.payments)
        amt = pl.payments[0][1]
        extra = ""
        if pl.changes:
            parts = []
            for s, kind, new, _ in pl.changes:
                name = s.description.lower()
                parts.append(f"stop the {name}" if kind == "stop" else f"reduce the {name} to {money(cur, new)}")
            extra = " and ".join(parts)
            extra = extra[0].upper() + extra[1:] + ", then use"
        else:
            extra = "Use"
        return (f"{extra} {n} installments of {money(cur, amt)}, starting {longdate(pl.payments[0][0])}. "
                f"This leaves at least {mn} available.")
    if pl.method == "wait":
        return (f"Pay {money(cur, R)} in full on {longdate(earliest)}. Paying earlier would take the balance "
                f"below the {mn} minimum.")
    return ""
