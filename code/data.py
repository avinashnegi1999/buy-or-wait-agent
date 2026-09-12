"""Load dataset CSVs, resolve currencies, and attach evidence (messages/images)."""
import csv
import os
from datetime import date

DATASET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dataset")


def _read(name):
    with open(os.path.join(DATASET, name), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def d(s):
    return date.fromisoformat(s) if s else None


def f(s):
    return float(s) if s not in (None, "") else None


class Data:
    def __init__(self):
        self.profiles = {r["user_id"]: r for r in _read("financial_profiles.csv")}
        self.requests = _read("requests.csv")
        self.samples = _read("sample_requests.csv")
        self.events = _read("financial_events.csv")
        self.options = _read("request_payment_options.csv")
        self.messages = _read("messages.csv")
        self.images = _read("images.csv")
        self.rates = {(r["rate_date"], r["from_currency"], r["to_currency"]): float(r["rate"])
                      for r in _read("exchange_rates.csv")}
        self.events_by_user = {}
        for e in self.events:
            self.events_by_user.setdefault(e["user_id"], []).append(e)
        self.event_by_id = {e["event_id"]: e for e in self.events}
        self.options_by_request = {}
        for o in self.options:
            self.options_by_request.setdefault(o["request_id"], []).append(o)
        self.messages_by_user = {}
        for m in self.messages:
            self.messages_by_user.setdefault(m["user_id"], []).append(m)
        self.images_by_event = {i["related_event_id"]: i for i in self.images if i["related_event_id"]}
        self.images_by_user = {}
        for i in self.images:
            self.images_by_user.setdefault(i["user_id"], []).append(i)

    def fx(self, amount, cur, home, on):
        """Convert amount from cur to home using the dated rate (either direction)."""
        if cur == home or amount is None:
            return amount
        key = (on.isoformat(), cur, home)
        if key in self.rates:
            return amount * self.rates[key]
        inv = (on.isoformat(), home, cur)
        if inv in self.rates:
            return amount / self.rates[inv]
        # fall back to nearest earlier dated rate for the pair
        cands = [(k[0], v, k[1] == cur) for k, v in self.rates.items()
                 if {k[1], k[2]} == {cur, home} and k[0] <= on.isoformat()]
        if not cands:
            raise KeyError(f"no fx rate {cur}->{home} on {on}")
        _, rate, direct = max(cands)
        return amount * rate if direct else amount / rate

    def image_path(self, image_id):
        return os.path.join(DATASET, "media", "images", f"{image_id}.png")
