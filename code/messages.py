"""Rule-based interpretation of messages.csv (English + Indonesian templates).

Every message is untrusted evidence. We only extract *financial facts* of known shapes
and never follow instructions embedded in the text. Output is a list of effects:

  ("salary_amount", amount, currency, from_date|None)  next/ongoing salary becomes amount
  ("salary_date", date)                                 payday moves to this date (monthly after)
  ("income_end",)                                       no further regular income
  ("income_only", amount, currency)                     one household income ended; remaining total
  ("one_off_credit", amount, currency, date)            confirmed single credit (invoice, arrears)
  ("no_recurring_income",)                              only confirmed one-offs count (freelance)
  ("rent_pct", pct)                                     rent increases by pct from next payment
  ("own_transfer",)                                     matching debit/credit are an internal transfer
  ("ignore", reason)                                    informational, no cash effect
"""
import re
from datetime import date

NUM = r"([A-Z]{3})\s*(\d[\d,]*(?:\.\d+)?)"
DATE = r"(\d{4}-\d{2}-\d{2})"


def _amt(s):
    return float(s.replace(",", ""))


def _d(s):
    return date.fromisoformat(s)


RULES = [
    # salary increased / naik
    (rf"(?:salary has increased to|Gaji bulanan Anda naik menjadi) {NUM}\.? .*?(?:applies from|berlaku mulai) {DATE}",
     lambda m: [("salary_amount", _amt(m[2]), m[1], _d(m[3]))]),
    # temporary reduced pay
    (rf"(?:temporary monthly pay is|Gaji bulanan sementara Anda adalah) {NUM}",
     lambda m: [("salary_amount", _amt(m[2]), m[1], None)]),
    # next salary reduced (unpaid leave)
    (rf"(?:next salary is reduced to|Gaji berikutnya dikurangi menjadi) {NUM}",
     lambda m: [("salary_amount", _amt(m[2]), m[1], None)]),
    # first salary (new job) on date
    (rf"(?:first salary(?: from the new employer)?(?: will be| is| of)?|Gaji pertama(?: dari perusahaan baru adalah| Anda sebesar)?) {NUM}\.? .*?"
     rf"(?:credit date is|confirmed for|scheduled for|dijadwalkan pada|dikonfirmasi untuk|dikonfirmasi adalah) {DATE}",
     lambda m: [("salary_amount", _amt(m[2]), m[1], _d(m[3]))]),
    # regular salary resumes on date (+ childcare with unknown amount: cannot be invented)
    (rf"(?:Regular salary of|Gaji rutin sebesar) {NUM} (?:resumes on|kembali pada) {DATE}",
     lambda m: [("salary_amount", _amt(m[2]), m[1], _d(m[3]))]),
    # payday moved
    (rf"(?:confirmed salary is now expected on|kini diperkirakan masuk pada) {DATE}",
     lambda m: [("salary_date", _d(m[1]))]),
    # income ended
    (r"seasonal contract has ended|Kontrak musiman saat ini telah berakhir|employment has ended|Hubungan kerja Anda telah berakhir",
     lambda m: [("income_end",)]),
    # one household income ended, remaining total
    (rf"(?:remaining confirmed monthly salary is|Sisa gaji bulanan yang dikonfirmasi adalah) {NUM}",
     lambda m: [("income_only", _amt(m[2]), m[1])]),
    # base salary confirmed, commission pending
    (rf"(?:confirmed base salary is|Gaji pokok yang dikonfirmasi adalah) {NUM}",
     lambda m: [("salary_amount", _amt(m[2]), m[1], None)]),
    # regular salary + one-time arrears in same payroll
    (rf"(?:regular salary for the next payroll is|Gaji rutin Anda untuk penggajian berikutnya adalah) {NUM}\.? .*?"
     rf"(?:arrears adjustment of|penyesuaian tunggakan satu kali sebesar) {NUM}",
     lambda m: [("salary_amount", _amt(m[2]), m[1], None), ("arrears", _amt(m[4]), m[3])]),
    # foreign salary confirmed for date (amount in foreign currency)
    (rf"(?:salary of|Gaji sebesar) {NUM} (?:is confirmed for|dikonfirmasi untuk) {DATE}",
     lambda m: [("salary_amount", _amt(m[2]), m[1], _d(m[3]))]),
    # approved invoice, settlement date; other invoices unconfirmed
    (rf"(?:approved an invoice payment of|menyetujui pembayaran faktur sebesar) {NUM}\.? .*?(?:expected on|diperkirakan pada) {DATE}",
     lambda m: [("no_recurring_income",), ("one_off_credit", _amt(m[2]), m[1], _d(m[3]))]),
    # rent increase
    (r"(?:increases monthly rent by|menaikkan biaya sewa bulanan sebesar) (\d+(?:\.\d+)?)%",
     lambda m: [("rent_pct", float(m[1]))]),
    # transfer between own accounts
    (r"transfer between your two accounts|transfer antara dua rekening Anda",
     lambda m: [("own_transfer",)]),
    # gig payout pending -> weekly app earnings are not confirmed income
    (r"payout is still pending|Pembayaran berikutnya dari \w+ masih tertunda",
     lambda m: [("no_recurring_income",)]),
    # everything below: no cash effect (pending credits, valuations, disputes, scams, receipts)
    (r"bonus is still subject|Bonus kuartalan Anda masih menunggu", lambda m: [("ignore", "bonus pending")]),
    (r"prize|hadiah", lambda m: [("ignore", "prize")]),
    (r"refund|Pengembalian dana", lambda m: [("ignore", "refund pending")]),
    (r"market value|Nilai investasi|displayed value", lambda m: [("ignore", "valuation")]),
    (r"investment sale|penjualan investasi", lambda m: [("ignore", "sale settled")]),
    (r"reimbursement|penggantian", lambda m: [("ignore", "reimbursement closed")]),
    (r"card charge is still being investigated|masih dalam penyelidikan", lambda m: [("ignore", "dispute")]),
    (r"debit attempt failed|another debit", lambda m: [("ignore", "retry scheduled")]),
    (r"foreign currency|mata uang asing", lambda m: [("ignore", "fx settles")]),
    (r"minimum payments due on two", lambda m: [("ignore", "two cards")]),
    (r"receipt|Receipt|wallet was charged", lambda m: [("ignore", "receipt")]),
    (r"Gaji rutin untuk penggajian berikutnya sudah dikonfirmasi", lambda m: [("ignore", "salary confirmed")]),
]


def parse_message(text):
    t = text.replace("’", "'")
    for pat, fn in RULES:
        m = re.search(pat, t, re.S)
        if m:
            return fn(m)
    return [("unparsed", t[:80])]


def effects_for_user(data, user_id, request_id, request_date):
    """Effects from messages of this user sent on/before request_date (newest last)."""
    msgs = [m for m in data.messages_by_user.get(user_id, [])
            if not m["request_id"] or m["request_id"] == request_id]
    msgs = [m for m in msgs if m["sent_at"][:10] <= request_date.isoformat()]
    msgs.sort(key=lambda m: m["sent_at"])
    out = []
    for m in msgs:
        for eff in parse_message(m["message_text"]):
            out.append((m["message_id"], m.get("related_event_id", ""), eff))
    return out


if __name__ == "__main__":
    # self-check: every message in the dataset must parse to a known effect
    from data import Data
    unparsed = 0
    for m in Data().messages:
        eff = parse_message(m["message_text"])
        if eff[0][0] == "unparsed":
            unparsed += 1
            print("UNPARSED", m["message_id"], m["message_text"][:120])
    print("unparsed:", unparsed)
    assert unparsed == 0
