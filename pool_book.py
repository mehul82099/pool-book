#!/usr/bin/env python3
"""pool-book: zero-dependency pooled group-order CLI for small retailers.

One small shop rarely meets a brand's minimum order quantity, so it buys
from a distributor at a margin. pool-book coordinates the pooled order:
record the brand's offer (unit price, MOQ, delivery charge, and the
distributor price for comparison), let retailers join with committed
quantities, and the book answers: has the pool reached MOQ, what does
each participant owe (goods plus an exact proportional delivery share),
what do they save versus the distributor price, who has received their
goods, and who has paid.

Python 3.9+, standard library only. One JSON file, offline, no accounts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import date

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "pool_book.json")


class PoolBookError(Exception):
    """User-facing error (bad input, unknown ref, ...)."""


def today_iso():
    return date.today().isoformat()


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise PoolBookError("bad date %r (expected YYYY-MM-DD)" % text)


def parse_amount(text, allow_zero=False):
    """Rupees string -> integer paise; max 2 decimals."""
    text = (text or "").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise PoolBookError(
            "bad amount %r (e.g. 48 or 48.50, paise beyond 2 decimals "
            "not accepted)" % text)
    rupees, _, frac = text.partition(".")
    frac = (frac + "00")[:2]
    paise = int(rupees) * 100 + int(frac)
    if paise < 0 or (paise == 0 and not allow_zero):
        raise PoolBookError("amount must be positive")
    return paise


def format_paise(paise):
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    s = str(paise // 100)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return "%s%s.%02d" % (sign, s, paise % 100)


def parse_count(text, what):
    try:
        n = int(text)
    except (TypeError, ValueError):
        raise PoolBookError("bad %s %r (expected a whole number)"
                            % (what, text))
    if n <= 0:
        raise PoolBookError("%s must be at least 1" % what)
    return n


def split_shares(total_paise, weights):
    """Split total_paise proportionally to weights (largest remainder,
    ties broken by position). Shares are whole paise and sum exactly."""
    if total_paise == 0 or not weights:
        return [0] * len(weights)
    base = sum(weights)
    exact = [(total_paise * w) / base for w in weights]
    floors = [int(x) for x in exact]
    remainder = total_paise - sum(floors)
    order = sorted(range(len(weights)),
                   key=lambda i: (exact[i] - floors[i], -i),
                   reverse=True)
    shares = list(floors)
    for i in order[:remainder]:
        shares[i] += 1
    return shares


def committed_qty(offer):
    return sum(p["qty"] for p in offer["participants"])


def moq_gap(offer):
    return max(0, offer["moq"] - committed_qty(offer))


def participant_totals(offer):
    """Per-participant rows: name, qty, goods, delivery share, total,
    distributor total and saving (paise)."""
    parts = offer["participants"]
    shares = split_shares(offer["delivery"], [p["qty"] for p in parts])
    rows = []
    for p, share in zip(parts, shares):
        goods = p["qty"] * offer["unit"]
        total = goods + share
        dist = (p["qty"] * offer["distributor_unit"]
                if offer["distributor_unit"] else None)
        rows.append({
            "retailer": p["retailer"], "qty": p["qty"], "goods": goods,
            "share": share, "total": total, "distributor_total": dist,
            "saving": (dist - total) if dist is not None else None,
            "received": p["received"], "paid": p["paid"],
        })
    return rows


class Book:
    def __init__(self):
        self.offers = {}  # ref -> offer dict

    @classmethod
    def load(cls, path):
        book = cls()
        if not os.path.exists(path):
            return book
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise PoolBookError("cannot read %s: %s" % (path, exc))
        book.offers = data.get("offers", {})
        return book

    def save(self, path):
        data = {"offers": self.offers}
        fd, tmp_name = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def get_offer(self, ref):
        try:
            return self.offers[ref]
        except KeyError:
            raise PoolBookError("unknown offer %r" % (ref,))

    def add_offer(self, ref, brand, product, unit, moq, delivery=0,
                  distributor_unit=None, valid_until=None):
        ref = (ref or "").strip()
        if not ref:
            raise PoolBookError("offer ref cannot be empty")
        if ref in self.offers:
            raise PoolBookError("offer ref %r already exists" % ref)
        if valid_until:
            parse_date(valid_until)
        self.offers[ref] = {
            "ref": ref, "brand": brand, "product": product, "unit": unit,
            "moq": moq, "delivery": delivery,
            "distributor_unit": distributor_unit,
            "valid_until": valid_until, "participants": [],
            "ordered": None, "created": today_iso()}
        return self.offers[ref]

    def join(self, ref, retailer, qty):
        offer = self.get_offer(ref)
        if offer["ordered"]:
            raise PoolBookError(
                "offer %s was already ordered on %s; new quantities cannot "
                "be added" % (ref, offer["ordered"]["date"]))
        retailer = (retailer or "").strip()
        if not retailer:
            raise PoolBookError("retailer name cannot be empty")
        if any(p["retailer"] == retailer for p in offer["participants"]):
            raise PoolBookError(
                "%s has already joined offer %s" % (retailer, ref))
        offer["participants"].append(
            {"retailer": retailer, "qty": qty, "received": 0,
             "paid": False})
        return offer

    def get_participant(self, offer, retailer):
        for p in offer["participants"]:
            if p["retailer"] == retailer:
                return p
        raise PoolBookError("%s has not joined offer %s"
                            % (retailer, offer["ref"]))

    def close_order(self, ref, when):
        offer = self.get_offer(ref)
        if offer["ordered"]:
            raise PoolBookError("offer %s was already ordered on %s"
                                % (ref, offer["ordered"]["date"]))
        if moq_gap(offer) > 0:
            raise PoolBookError(
                "pool is %d units short of the MOQ %d; cannot order yet"
                % (moq_gap(offer), offer["moq"]))
        parse_date(when)
        offer["ordered"] = {"date": when}
        return offer

    def record_receipt(self, ref, retailer, qty, when):
        offer = self.get_offer(ref)
        if not offer["ordered"]:
            raise PoolBookError(
                "offer %s has not been ordered yet; close the pool first"
                % ref)
        p = self.get_participant(offer, retailer)
        parse_date(when)
        if p["received"] + qty > p["qty"]:
            raise PoolBookError(
                "%s committed %d units and already received %d; %d more "
                "would exceed the commitment"
                % (retailer, p["qty"], p["received"], qty))
        p["received"] += qty
        return p

    def record_payment(self, ref, retailer, when):
        offer = self.get_offer(ref)
        if not offer["ordered"]:
            raise PoolBookError(
                "offer %s has not been ordered yet; close the pool first"
                % ref)
        p = self.get_participant(offer, retailer)
        if p["paid"]:
            raise PoolBookError("%s has already paid for offer %s"
                                % (retailer, ref))
        parse_date(when)
        p["paid"] = True
        return p

    def summary(self):
        open_pools = [o for o in self.offers.values() if not o["ordered"]]
        ordered = [o for o in self.offers.values() if o["ordered"]]
        unpaid = 0
        undelivered = 0
        for o in ordered:
            for p in o["participants"]:
                if not p["paid"]:
                    unpaid += 1
                if p["received"] < p["qty"]:
                    undelivered += 1
        return {"offers": len(self.offers), "open": len(open_pools),
                "ordered": len(ordered), "unpaid": unpaid,
                "undelivered": undelivered}

    def export_csv(self, out_path):
        fields = ["offer", "brand", "product", "retailer", "qty", "goods",
                  "delivery_share", "total", "distributor_total", "saving",
                  "received", "paid", "ordered_on"]
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(fields)
            for offer in self.offers.values():
                for row in participant_totals(offer):
                    writer.writerow([
                        offer["ref"], offer["brand"], offer["product"],
                        row["retailer"], row["qty"],
                        format_paise(row["goods"]),
                        format_paise(row["share"]),
                        format_paise(row["total"]),
                        (format_paise(row["distributor_total"])
                         if row["distributor_total"] is not None else ""),
                        (format_paise(row["saving"])
                         if row["saving"] is not None else ""),
                        row["received"], "yes" if row["paid"] else "no",
                        offer["ordered"]["date"] if offer["ordered"]
                        else "",
                    ])


def cmd_offer(book, args):
    distributor = (parse_amount(args.distributor_unit)
                   if args.distributor_unit else None)
    offer = book.add_offer(
        args.ref, args.brand, args.product, parse_amount(args.unit),
        parse_count(args.moq, "MOQ"),
        delivery=parse_amount(args.delivery, allow_zero=True),
        distributor_unit=distributor, valid_until=args.valid_until)
    print("recorded offer %s: %s %s at %s/unit, MOQ %d, delivery %s%s" % (
        offer["ref"], offer["brand"], offer["product"],
        format_paise(offer["unit"]), offer["moq"],
        format_paise(offer["delivery"]),
        ", distributor %s/unit" % format_paise(distributor)
        if distributor else ""))


def cmd_join(book, args):
    offer = book.join(args.ref, args.retailer, parse_count(args.qty, "qty"))
    gap = moq_gap(offer)
    state = "MOQ met" if gap == 0 else "%d units short of MOQ" % gap
    print("%s joined %s with %d units (pool at %d of %d, %s)" % (
        args.retailer, args.ref, parse_count(args.qty, "qty"),
        committed_qty(offer), offer["moq"], state))


def cmd_pool(book, args):
    offer = book.get_offer(args.ref)
    gap = moq_gap(offer)
    state = "ordered on %s" % offer["ordered"]["date"] if offer["ordered"] \
        else ("MOQ met, ready to order" if gap == 0
              else "%d units short of MOQ %d" % (gap, offer["moq"]))
    print("%s: %s %s at %s/unit, MOQ %d, delivery %s - %s" % (
        offer["ref"], offer["brand"], offer["product"],
        format_paise(offer["unit"]), offer["moq"],
        format_paise(offer["delivery"]), state))
    if not offer["participants"]:
        print("  no participants yet")
        return
    for row in participant_totals(offer):
        line = ("  %-22s %4d units  goods %12s + delivery %9s = %12s"
                % (row["retailer"], row["qty"], format_paise(row["goods"]),
                   format_paise(row["share"]), format_paise(row["total"])))
        if row["saving"] is not None:
            line += "  saves %s vs distributor" % format_paise(row["saving"])
        if offer["ordered"]:
            line += "  [received %d/%d%s]" % (
                row["received"], row["qty"],
                ", paid" if row["paid"] else "")
        print(line)


def cmd_pools(book, args):
    if not book.offers:
        print("no offers yet")
        return
    for ref in sorted(book.offers):
        offer = book.offers[ref]
        gap = moq_gap(offer)
        state = "ordered %s" % offer["ordered"]["date"] if offer["ordered"] \
            else ("MOQ met" if gap == 0 else "%d short" % gap)
        print("  %-10s %-24s pool %d/%d (%s), %d retailer(s)" % (
            ref, "%s %s" % (offer["brand"], offer["product"]),
            committed_qty(offer), offer["moq"], state,
            len(offer["participants"])))


def cmd_close(book, args):
    offer = book.close_order(args.ref, args.date)
    print("ordered offer %s on %s: %d units across %d retailer(s)" % (
        args.ref, args.date, committed_qty(offer),
        len(offer["participants"])))


def cmd_receive(book, args):
    p = book.record_receipt(args.ref, args.retailer,
                            parse_count(args.qty, "qty"), args.date)
    left = p["qty"] - p["received"]
    print("recorded %d units received by %s on %s (%s)" % (
        parse_count(args.qty, "qty"), args.retailer, args.date,
        "complete" if left == 0 else "%d still due" % left))


def cmd_pay(book, args):
    book.record_payment(args.ref, args.retailer, args.date)
    print("recorded payment from %s for %s on %s"
          % (args.retailer, args.ref, args.date))


def cmd_export(book, args):
    book.export_csv(args.path)
    print("wrote %s" % args.path)


def cmd_summary(book, args):
    s = book.summary()
    print("offers: %d (open %d, ordered %d), unpaid participants: %d, "
          "incomplete deliveries: %d"
          % (s["offers"], s["open"], s["ordered"], s["unpaid"],
             s["undelivered"]))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pool-book",
        description="Pooled group-order book: reach a brand's MOQ "
                    "together, split costs exactly, track delivery and "
                    "payment.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help="JSON data file (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("offer", help="record a brand offer")
    p.add_argument("ref")
    p.add_argument("brand")
    p.add_argument("product")
    p.add_argument("--unit", required=True, help="brand price per unit")
    p.add_argument("--moq", required=True, help="brand minimum order qty")
    p.add_argument("--delivery", default="0",
                   help="total delivery charge in rupees (default 0)")
    p.add_argument("--distributor-unit", default=None,
                   help="distributor price per unit, for savings")
    p.add_argument("--valid-until", default=None, help="YYYY-MM-DD")
    p.set_defaults(func=cmd_offer)

    p = sub.add_parser("join", help="a retailer joins the pool")
    p.add_argument("ref")
    p.add_argument("retailer")
    p.add_argument("qty")
    p.set_defaults(func=cmd_join)

    p = sub.add_parser("pool", help="one offer's pool and cost split")
    p.add_argument("ref")
    p.set_defaults(func=cmd_pool)

    p = sub.add_parser("pools", help="all offers at a glance")
    p.set_defaults(func=cmd_pools)

    p = sub.add_parser("close", help="place the pooled order")
    p.add_argument("ref")
    p.add_argument("date", help="order date, YYYY-MM-DD")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("receive", help="record goods received by a retailer")
    p.add_argument("ref")
    p.add_argument("retailer")
    p.add_argument("qty")
    p.add_argument("date", help="YYYY-MM-DD")
    p.set_defaults(func=cmd_receive)

    p = sub.add_parser("pay", help="record a retailer's payment")
    p.add_argument("ref")
    p.add_argument("retailer")
    p.add_argument("date", help="YYYY-MM-DD")
    p.set_defaults(func=cmd_pay)

    p = sub.add_parser("export", help="export pools to CSV")
    p.add_argument("path")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("summary", help="one-line totals")
    p.set_defaults(func=cmd_summary)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        book = Book.load(args.db)
        args.func(book, args)
        book.save(args.db)
    except PoolBookError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
