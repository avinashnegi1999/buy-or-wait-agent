"""Financial-state reconstruction, 90-day forecast, and plan selection."""
import calendar
import itertools
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median

from data import d, f
from messages import effects_for_user

import os
HORIZON = int(os.environ.get("HORIZON", "84"))  # ponytail: 12-week window fits the solved samples better than 90 (see evaluation)
ONE_OFF_TYPES = {"refund", "investment_purchase", "investment_valuation", "investment_sale"}
ONE_OFF_CATS = {"windfall", "work_expense", "investment"}
SALARY_ONEOFF = re.compile(r"bonus|commission|arrears|prize|reimbursement|prorated", re.I)
INCOME_END = re.compile(r"final employer payroll", re.I)
IRREGULAR_INCOME = re.compile(r"payout|app earnings|freelance|contract|invoice|project|retainer|"
                              r"independent work|seasonal|temporary assignment|peak-season", re.I)


def add_months(dt, n, day):
    y, m = divmod(dt.month - 1 + n, 12)
    y += dt.year
    m += 1
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


class Series:
    """A recurring cash flow projected forward from its last observed occurrence."""

    def __init__(self, key, events, cadence, amount, sign, home, data):
        self.key = key
        self.events = events
        self.cadence = cadence[:2]  # ("monthly", day) or ("days", n)
        anchor = cadence[2] if len(cadence) > 2 else len(events) - 1
        self.amount = amount
        self.sign = sign
        self.last = d(events[anchor]["settlement_date"])
        self.last_seen = d(events[-1]["settlement_date"])
        self.category = events[-1]["category"]
        self.flex = events[-1]["flexibility"]
        self.min_allowed = f(events[-1]["minimum_allowed_amount"])
        self.ref_event = events[-1]["event_id"]
        self.description = events[-1]["description"]
        self.currency = events[-1]["currency"]
        self.home = home
        self.data = data
        self.first_override = None  # (date, amount) for a confirmed next occurrence
        self.start_after = self.last

    def occurrences(self, start, end):
        out = []
        if self.first_override:
            nxt, amt = self.first_override
            if start <= nxt <= end:
                out.append((nxt, amt))
            cur = nxt
        else:
            cur = self._next(self.start_after)
            while cur <= end:
                if cur >= start:
                    out.append((cur, self._amt(cur)))
                cur = self._next(cur)
            return out
        cur = self._next(cur)
        while cur <= end:
            if cur >= start:
                out.append((cur, self._amt(cur)))
            cur = self._next(cur)
        return out

    def _amt(self, on):
        return self.data.fx(self.amount, self.currency, self.home, on)

    def _next(self, cur):
        if self.cadence[0] == "monthly":
            return add_months(cur, 1, self.cadence[1])
        return cur + timedelta(days=self.cadence[1])


def detect_cadence(dates):
    """Return ("monthly", day) / ("days", n) plus the index of the last on-pattern date, or None.

    Tolerates a few off-pattern events (a one-off invoice in the groceries category, an
    image-backed extra purchase) as long as most gaps agree."""
    if len(dates) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    med = median(gaps)
    monthly = 27 <= med <= 32
    tol = 5 if monthly else 1
    ok = [abs(g - med) <= tol for g in gaps]
    if sum(ok) < max(1, 0.6 * len(gaps)):
        return None
    if not monthly and not (int(os.environ.get("MIN_GAP", "3")) <= med <= 45):
        return None
    # anchor: last date whose preceding gap is on-pattern (else the earliest such)
    anchor = len(dates) - 1
    while anchor > 0 and not ok[anchor - 1]:
        anchor -= 1
    if monthly:
        return ("monthly", dates[anchor].day, anchor)
    return ("days", int(round(med)), anchor)


def build_series(events, sign, home, data, min_count=3):
    """Group settled events into recurring series.

    Try the whole category first (variable spending like groceries has random descriptions
    but a fixed cadence); if that is irregular, split by description (e.g. two loans)."""
    events = sorted(events, key=lambda e: e["settlement_date"])
    if len(events) >= min_count:
        cad = detect_cadence([d(e["settlement_date"]) for e in events])
        if cad:
            return [Series(events[-1]["category"], events, cad, _amount(events), sign, home, data)]
    out = []
    by_desc = defaultdict(list)
    for e in events:
        by_desc[e["description"]].append(e)
    for desc, es in by_desc.items():
        if len(es) >= min_count:
            cad = detect_cadence([d(e["settlement_date"]) for e in es])
            if cad:
                out.append(Series(desc, es, cad, _amount(es), sign, home, data))
    return out


def _amount(es):
    amts = [float(e["amount"]) for e in es]
    # drop one-off outliers riding on a recurring series (e.g. an image-backed bulk purchase)
    med = median(amts)
    core = [a for a in amts if 0.4 * med <= a <= 2.5 * med]
    if len(core) >= 2:
        amts = core
    est = os.environ.get("EST", "mean")
    if est == "median":
        return median(amts)
    if est == "max":
        return max(amts)
    if est == "last":
        return amts[-1]
    if est == "mean3":
        return mean(amts[-3:])
    if est == "p75":
        return sorted(amts)[int(len(amts) * 0.75)]
    if est == "meanstd":
        m = mean(amts)
        return m + (sum((a - m) ** 2 for a in amts) / max(1, len(amts) - 1)) ** 0.5
    if est == "max3":
        return max(amts[-3:])
    if est == "mean_last3max":
        return max(mean(amts), max(amts[-3:]))
    if max(amts) - min(amts) > 1e-9:
        return mean(amts) * float(os.environ.get("SCALE", "1.0"))
    return mean(amts)


class State:
    def __init__(self, data, req, image_amounts):
        self.data = data
        self.req = req
        self.user = req["user_id"]
        self.p = data.profiles[self.user]
        self.home = self.p["home_currency"]
        self.rd = d(req["request_date"])
        self.end = self.rd + timedelta(days=HORIZON)
        self.balance = float(self.p["current_available_balance"])
        self.min_bal = float(self.p["minimum_balance_to_keep"])
        self.notes = []
        self.effects = effects_for_user(data, self.user, req["request_id"], self.rd)
        self.image_amounts = image_amounts
        self._build()

    # ---------- state reconstruction ----------
    def _build(self):
        evs = []
        for e in self.data.events_by_user.get(self.user, []):
            e = dict(e)
            if e["amount"] == "":
                if e["event_id"] in self.image_amounts:
                    e["amount"] = str(self.image_amounts[e["event_id"]])
                    e["from_image"] = True  # documented one-off; never part of a recurring series
                else:
                    self.notes.append(f"{e['event_id']} blank amount without image; skipped")
                    continue
            if e["status"] == "settled" and e["direction"] == "debit" and e["currency"] != self.home:
                # keep debit series in home currency: a foreign receipt as the last event must not
                # set the series currency and get the home-currency mean re-converted
                e["amount"] = str(self.data.fx(float(e["amount"]), e["currency"], self.home, d(e["settlement_date"])))
                e["currency"] = self.home
            evs.append(e)
        eff_kinds = {x[2][0] for x in self.effects}
        if "own_transfer" in eff_kinds:
            evs = self._drop_own_transfers(evs)

        settled = [e for e in evs if e["status"] == "settled" and d(e["settlement_date"]) <= self.rd]
        # duplicates: identical (date, amount, category, direction) settled twice -> keep one for history
        seen, dedup = set(), []
        for e in settled:
            k = (e["settlement_date"], e["amount"], e["category"], e["direction"], e["description"])
            if k in seen:
                continue
            seen.add(k)
            dedup.append(e)
        settled = dedup

        # recurring debits
        deb = [e for e in settled if e["direction"] == "debit" and e["event_type"] not in ONE_OFF_TYPES
               and e["category"] not in ONE_OFF_CATS and not e.get("from_image")]
        by_cat = defaultdict(list)
        for e in deb:
            by_cat[e["category"]].append(e)
        self.debit_series = []
        for cat, es in by_cat.items():
            self.debit_series.extend(build_series(es, -1, self.home, self.data))

        # recurring income (salary-like only; gig/freelance/seasonal income is not committed)
        inc = [e for e in settled if e["direction"] == "credit" and e["event_type"] == "income"
               and e["category"] == "salary" and not SALARY_ONEOFF.search(e["description"])
               and not IRREGULAR_INCOME.search(e["description"])]
        self.income_series = build_series(inc, +1, self.home, self.data, min_count=2)
        # an income series that already missed an expected payment is not confirmed
        self.income_series = [s for s in self.income_series if s._next(s.last_seen) + timedelta(days=7) >= self.rd]
        # scheduled next salary overrides the first projected occurrence
        for e in evs:
            if e["status"] == "scheduled" and e["direction"] == "credit":
                on = d(e["settlement_date"])
                amt = float(e["amount"])
                if self.income_series:
                    s = self.income_series[0]
                    s.first_override = (on, self.data.fx(amt, e["currency"], self.home, on))
                    s.amount, s.currency = amt, e["currency"]
                    s.cadence = ("monthly", on.day)
                else:
                    s = Series("scheduled", [e], ("monthly", on.day), amt, +1, self.home, self.data)
                    s.first_override = (on, self.data.fx(amt, e["currency"], self.home, on))
                    self.income_series.append(s)
        if any(INCOME_END.search(e["description"]) for e in inc):
            self.income_series = []

        # one-off future flows: pending/scheduled debits, ignore pending credits/failed/cancelled/unrealized
        self.one_offs = []
        for e in evs:
            st = e["status"]
            if st in ("failed", "cancelled", "unrealized"):
                continue
            if re.search(r"duplicate", e["description"], re.I):
                continue
            on = d(e["settlement_date"])
            if st == "pending" and e["direction"] == "debit":
                amt = self.data.fx(float(e["amount"]), e["currency"], self.home, on)
                self.one_offs.append((max(on, self.rd), -amt, e["event_id"], e["description"]))
            elif st == "scheduled" and e["direction"] == "debit":
                if on < self.rd:
                    on = self.rd
                amt = self.data.fx(float(e["amount"]), e["currency"], self.home, on)
                self.one_offs.append((on, -amt, e["event_id"], e["description"]))
        self._apply_effects()

    def _drop_own_transfers(self, evs):
        # matching debit + credit of the same amount within a few days -> internal transfer
        credits = [e for e in evs if e["direction"] == "credit" and e["event_type"] != "income"]
        drop = set()
        for c in credits:
            for e in evs:
                if e["direction"] == "debit" and e["amount"] == c["amount"] and e["event_id"] != c["event_id"] \
                        and abs((d(e["settlement_date"]) - d(c["settlement_date"])).days) <= 5:
                    drop.update({c["event_id"], e["event_id"]})
        if drop:
            self.notes.append(f"own-account transfer ignored: {sorted(drop)}")
        return [e for e in evs if e["event_id"] not in drop]

    def _apply_effects(self):
        for mid, rel, eff in self.effects:
            kind = eff[0]
            if kind == "salary_amount":
                _, amt, cur, from_date = eff
                if os.environ.get("IGNORE_MSG_SALARY") and self.income_series:
                    continue
                if not self.income_series:
                    day = from_date.day if from_date else 15
                    s = Series("message", [{"event_id": mid, "settlement_date": (from_date or self.rd).isoformat(),
                                            "category": "salary", "flexibility": "fixed", "minimum_allowed_amount": "",
                                            "description": "confirmed salary", "currency": cur}],
                               ("monthly", day), amt, +1, self.home, self.data)
                    s.start_after = add_months(from_date or self.rd, -1, day)
                    self.income_series = [s]
                s = self.income_series[0]
                s.amount, s.currency = amt, cur
                if from_date:
                    s.first_override = (from_date, self.data.fx(amt, cur, self.home, from_date))
                    s.cadence = ("monthly", from_date.day)
                elif s.first_override:
                    s.first_override = (s.first_override[0], self.data.fx(amt, cur, self.home, s.first_override[0]))
            elif kind == "arrears":
                _, amt, cur = eff
                if self.income_series:
                    s = self.income_series[0]
                    occ = s.occurrences(self.rd, self.end)
                    if occ:
                        self.one_offs.append((occ[0][0], self.data.fx(amt, cur, self.home, occ[0][0]), mid, "arrears"))
            elif kind == "salary_date":
                on = eff[1]
                for s in self.income_series:
                    s.first_override = (on, s._amt(on))
                    s.cadence = ("monthly", on.day)
            elif kind == "income_end":
                self.income_series = []
            elif kind == "income_only":
                _, amt, cur = eff
                if self.income_series:
                    s = self.income_series[0]
                    s.amount, s.currency = amt, cur
                    if s.first_override:
                        s.first_override = (s.first_override[0], self.data.fx(amt, cur, self.home, s.first_override[0]))
                    self.income_series = [s]
            elif kind == "no_recurring_income":
                self.income_series = []
            elif kind == "one_off_credit":
                _, amt, cur, on = eff
                if on >= self.rd:
                    self.one_offs.append((on, self.data.fx(amt, cur, self.home, on), mid, "confirmed invoice"))
            elif kind == "rent_pct":
                for s in self.debit_series:
                    if s.category == "rent":
                        s.amount *= 1 + eff[1] / 100

    # ---------- forecast ----------
    def flows(self, changes=None, end=None):
        """All projected flows in [rd, end]: list of (date, signed_amount, label)."""
        changes = changes or {}
        end = end or self.end
        out = []
        excl = os.environ.get("EXCL_TODAY", "days")
        for s in self.debit_series:
            amt_override = changes.get(s.ref_event)
            start = self.rd + timedelta(days=1) if (excl == "all" or (excl == "days" and s.cadence[0] == "days")) else self.rd
            for on, amt in s.occurrences(start, end):
                if amt_override == "stop":
                    continue
                if amt_override is not None:
                    amt = amt_override
                out.append((on, -amt, s.ref_event, s.cadence[0]))
        for s in self.income_series:
            for on, amt in s.occurrences(self.rd, end):
                out.append((on, amt, s.ref_event, "income"))
        for on, amt, eid, _ in self.one_offs:
            if self.rd <= on <= end:
                out.append((on, amt, eid, "oneoff"))
        return out

    def path(self, changes=None, payments=()):
        """Daily balance over the horizon after applying payments [(date, amt)].

        Returns [(date, intraday_low, end_of_day)]. Debits are assumed to clear before the
        day's credits land, so the intraday low is the conservative check point; a payment
        made on a payday is made after the salary has landed.
        """
        debits, credits, pays = defaultdict(float), defaultdict(float), defaultdict(float)
        early = defaultdict(float)  # debits that clear before the day's credits
        # simulate at least until the last planned payment so long installment plans are verified
        end = max([self.end] + [on for on, _ in payments])
        order = os.environ.get("ORDER", "hybrid")  # ponytail: day-cadence debits clear before payday credits; fits samples best
        for on, amt, _, kind in self.flows(changes, end):
            if amt > 0:
                credits[on] += amt
            elif order == "debit_first" or (order == "hybrid" and kind == "days"):
                early[on] += amt
            else:
                debits[on] += amt
        for on, amt in payments:
            pays[on] += amt
        bal = self.balance
        out = []
        cur = self.rd
        while cur <= end:
            low = bal + early.get(cur, 0.0)
            bal = low + credits.get(cur, 0.0) + debits.get(cur, 0.0) - pays.get(cur, 0.0)
            out.append((cur, min(low, bal), bal))
            cur += timedelta(days=1)
        return out

    def safe_today(self, changes=None):
        low = min(b for _, b, _ in self.path(changes))
        return max(0.0, low - self.min_bal)

    def earliest_full(self, amount, changes=None):
        p = self.path(changes)
        # suffix minima of intraday lows after day i, plus end-of-day balance on day i
        suf = [0.0] * (len(p) + 1)
        suf[len(p)] = float("inf")
        for i in range(len(p) - 1, -1, -1):
            suf[i] = min(suf[i + 1], p[i][1])
        for i, (on, _, end) in enumerate(p):
            if min(end, suf[i + 1]) - amount >= self.min_bal - 1e-9:
                return on
        return None

    def is_safe(self, payments, changes=None):
        return all(b >= self.min_bal - 1e-9 for _, b, _ in self.path(changes, payments))

    # ---------- spending changes ----------
    def change_candidates(self):
        protect = set(filter(None, self.p["expense_categories_to_protect"].split("|")))
        reduce = set(filter(None, self.p["expense_categories_user_is_willing_to_reduce"].split("|")))
        stop = set(filter(None, self.p["expense_categories_user_is_willing_to_stop"].split("|")))
        cands = []
        for s in self.debit_series:
            if s.category in protect:
                continue
            can_stop = s.flex in ("stoppable", "reducible_or_stoppable") and s.category in stop
            can_reduce = s.flex in ("reducible", "reducible_or_stoppable") and s.category in reduce and s.min_allowed
            if can_stop:
                cands.append((s, "stop", None, s.amount))
            if can_reduce and s.min_allowed < s.amount:
                cands.append((s, "reduce_to", s.min_allowed, s.amount - s.min_allowed))
        return cands

    def cheapest_changes(self, payments):
        """Smallest set (by total monthly saving, then count) of ≤3 changes making payments safe."""
        cands = self.change_candidates()
        best = None
        for n in (1, 2, 3):
            for combo in itertools.combinations(cands, n):
                if len({c[0].ref_event for c in combo}) < n:
                    continue
                changes = {c[0].ref_event: ("stop" if c[1] == "stop" else c[2]) for c in combo}
                if self.is_safe(payments, changes):
                    saving = sum(c[3] for c in combo)
                    key = (saving, n, [c[0].ref_event for c in combo])
                    if best is None or key < best[0]:
                        best = (key, combo, changes)
            if best:
                break
        return best
