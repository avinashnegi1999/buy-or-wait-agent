"""Dev harness: compare forecast primitives against sample_requests.csv."""
import sys
from data import Data, d
from engine import State
from images import extract_amounts


class NoUsage:
    def record(self, *a):
        pass


data = Data()
imgs = extract_amounts(data, NoUsage())
only = sys.argv[1:]  # optional request ids
for r in data.samples:
    if only and r["request_id"] not in only:
        continue
    st = State(data, r, imgs)
    R = float(r["requested_amount"])
    safe = min(R, st.safe_today())
    earl = st.earliest_full(R)
    ok1 = abs(safe - float(r["amount_safe_to_pay"])) < 0.01
    ok2 = (earl.isoformat() if earl else "") == r["earliest_date_for_full_payment"]
    print(f"{r['request_id']} {r['user_id']} safe {safe:.2f} vs {r['amount_safe_to_pay']} {'OK' if ok1 else 'XX'} | "
          f"earliest {earl} vs {r['earliest_date_for_full_payment'] or '-'} {'OK' if ok2 else 'XX'}")
    if only:
        print("  balance", st.balance, "min", st.min_bal, "notes", st.notes, "effects", [e[2] for e in st.effects])
        for s in st.debit_series + st.income_series:
            print("  series", s.key, s.category, s.cadence, round(s.amount, 2), s.currency, "last", s.last, "ref", s.ref_event, s.flex, s.min_allowed, "override", s.first_override)
        print("  one_offs", st.one_offs)
        p = st.path()
        low = min(p, key=lambda x: x[1])
        print("  low", low)
        for on, lo, bal in p:
            fl = [x[:3] for x in st.flows() if x[0] == on]
            if fl:
                print("   ", on, round(bal, 2), [(x[2], round(x[1], 2)) for x in fl])
