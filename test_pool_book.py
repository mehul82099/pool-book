#!/usr/bin/env python3
"""Tests for pool_book. Run: python3 -m unittest test_pool_book -v"""

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest

import pool_book as pb


def book_with_offer():
    book = pb.Book()
    book.add_offer("POOL-1", "AMULYA", "ghee 1L", unit=42000, moq=100,
                   delivery=300000, distributor_unit=52000,
                   valid_until="2026-12-31")
    return book


class ParseTest(unittest.TestCase):
    def test_parse_amount(self):
        self.assertEqual(pb.parse_amount("420"), 42000)
        self.assertEqual(pb.parse_amount("48.50"), 4850)
        self.assertEqual(pb.parse_amount("0", allow_zero=True), 0)
        with self.assertRaises(pb.PoolBookError):
            pb.parse_amount("0")
        for bad in ("abc", "1.234", "1,000", "-5"):
            with self.assertRaises(pb.PoolBookError, msg=bad):
                pb.parse_amount(bad)

    def test_format_paise_indian_grouping(self):
        self.assertEqual(pb.format_paise(123456789), "12,34,567.89")
        self.assertEqual(pb.format_paise(42000), "420.00")

    def test_parse_count(self):
        self.assertEqual(pb.parse_count("100", "MOQ"), 100)
        for bad in ("0", "-3", "ten"):
            with self.assertRaises(pb.PoolBookError, msg=bad):
                pb.parse_count(bad, "MOQ")


class ShareSplitTest(unittest.TestCase):
    def test_exact_division(self):
        self.assertEqual(pb.split_shares(300000, [1, 1, 1]),
                         [100000, 100000, 100000])

    def test_largest_remainder_sums_exactly(self):
        shares = pb.split_shares(100, [1, 1, 1])
        self.assertEqual(sum(shares), 100)
        self.assertEqual(shares, [34, 33, 33])

    def test_proportional_weights(self):
        shares = pb.split_shares(300000, [50, 30, 20])
        self.assertEqual(shares, [150000, 90000, 60000])

    def test_zero_total_and_empty(self):
        self.assertEqual(pb.split_shares(0, [1, 2]), [0, 0])
        self.assertEqual(pb.split_shares(500, []), [])

    def test_remainder_deterministic_order(self):
        # ties broken by position: earlier participant gets the extra paisa
        self.assertEqual(pb.split_shares(1, [1, 1]), [1, 0])


class OfferTest(unittest.TestCase):
    def test_add_offer(self):
        book = book_with_offer()
        offer = book.get_offer("POOL-1")
        self.assertEqual(offer["moq"], 100)
        self.assertIsNone(offer["ordered"])

    def test_duplicate_offer_rejected(self):
        book = book_with_offer()
        with self.assertRaises(pb.PoolBookError):
            book.add_offer("POOL-1", "B", "p", 100, 10)

    def test_unknown_offer(self):
        with self.assertRaises(pb.PoolBookError):
            pb.Book().get_offer("NOPE")

    def test_bad_valid_until_rejected(self):
        with self.assertRaises(pb.PoolBookError):
            pb.Book().add_offer("X", "B", "p", 100, 10,
                                valid_until="tomorrow")


class JoinTest(unittest.TestCase):
    def setUp(self):
        self.book = book_with_offer()

    def test_join_and_gap(self):
        self.book.join("POOL-1", "SHARMA STORES", 40)
        offer = self.book.get_offer("POOL-1")
        self.assertEqual(pb.committed_qty(offer), 40)
        self.assertEqual(pb.moq_gap(offer), 60)
        self.book.join("POOL-1", "GUPTA MART", 60)
        self.assertEqual(pb.moq_gap(offer), 0)

    def test_duplicate_retailer_rejected(self):
        self.book.join("POOL-1", "SHARMA STORES", 40)
        with self.assertRaises(pb.PoolBookError):
            self.book.join("POOL-1", "SHARMA STORES", 10)

    def test_empty_retailer_rejected(self):
        with self.assertRaises(pb.PoolBookError):
            self.book.join("POOL-1", " ", 10)


class TotalsTest(unittest.TestCase):
    def setUp(self):
        self.book = book_with_offer()
        self.book.join("POOL-1", "SHARMA STORES", 50)
        self.book.join("POOL-1", "GUPTA MART", 30)
        self.book.join("POOL-1", "KHAN PROVISIONS", 20)

    def test_participant_totals(self):
        rows = pb.participant_totals(self.book.get_offer("POOL-1"))
        by_name = {r["retailer"]: r for r in rows}
        # unit 420.00, delivery 3,000.00 split 50/30/20 -> 1500/900/600
        sharma = by_name["SHARMA STORES"]
        self.assertEqual(sharma["goods"], 2100000)     # 50 x 420.00
        self.assertEqual(sharma["share"], 150000)
        self.assertEqual(sharma["total"], 2250000)     # 22,500.00
        self.assertEqual(sharma["distributor_total"], 2600000)  # 50 x 520
        self.assertEqual(sharma["saving"], 350000)     # 3,500.00
        khan = by_name["KHAN PROVISIONS"]
        self.assertEqual(khan["total"], 900000)        # 20x420 + 600
        # shares sum exactly to delivery
        self.assertEqual(sum(r["share"] for r in rows), 300000)

    def test_saving_none_without_distributor_price(self):
        book = pb.Book()
        book.add_offer("P2", "BRAND", "item", 100, 10)
        book.join("P2", "A SHOP", 10)
        row = pb.participant_totals(book.get_offer("P2"))[0]
        self.assertIsNone(row["saving"])
        self.assertIsNone(row["distributor_total"])


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.book = book_with_offer()
        self.book.join("POOL-1", "SHARMA STORES", 60)
        self.book.join("POOL-1", "GUPTA MART", 40)

    def test_close_below_moq_rejected(self):
        book = book_with_offer()
        book.join("POOL-1", "SMALL SHOP", 50)
        with self.assertRaises(pb.PoolBookError):
            book.close_order("POOL-1", "2026-11-24")

    def test_close_and_double_close(self):
        self.book.close_order("POOL-1", "2026-11-24")
        self.assertEqual(self.book.get_offer("POOL-1")["ordered"]["date"],
                         "2026-11-24")
        with self.assertRaises(pb.PoolBookError):
            self.book.close_order("POOL-1", "2026-11-25")

    def test_join_after_close_rejected(self):
        self.book.close_order("POOL-1", "2026-11-24")
        with self.assertRaises(pb.PoolBookError):
            self.book.join("POOL-1", "LATE SHOP", 10)

    def test_receive_before_close_rejected(self):
        with self.assertRaises(pb.PoolBookError):
            self.book.record_receipt("POOL-1", "SHARMA STORES", 10,
                                     "2026-11-25")

    def test_receive_partial_and_overflow(self):
        self.book.close_order("POOL-1", "2026-11-24")
        p = self.book.record_receipt("POOL-1", "SHARMA STORES", 40,
                                     "2026-11-28")
        self.assertEqual(p["received"], 40)
        with self.assertRaises(pb.PoolBookError):
            self.book.record_receipt("POOL-1", "SHARMA STORES", 21,
                                     "2026-11-29")
        p = self.book.record_receipt("POOL-1", "SHARMA STORES", 20,
                                     "2026-11-29")
        self.assertEqual(p["received"], 60)

    def test_pay_rules(self):
        self.book.close_order("POOL-1", "2026-11-24")
        p = self.book.record_payment("POOL-1", "GUPTA MART", "2026-11-30")
        self.assertTrue(p["paid"])
        with self.assertRaises(pb.PoolBookError):
            self.book.record_payment("POOL-1", "GUPTA MART", "2026-12-01")
        with self.assertRaises(pb.PoolBookError):
            self.book.record_payment("POOL-1", "GHOST", "2026-12-01")

    def test_pay_before_close_rejected(self):
        with self.assertRaises(pb.PoolBookError):
            self.book.record_payment("POOL-1", "GUPTA MART", "2026-11-24")

    def test_summary_counts(self):
        self.book.close_order("POOL-1", "2026-11-24")
        self.book.record_payment("POOL-1", "GUPTA MART", "2026-11-30")
        self.book.record_receipt("POOL-1", "GUPTA MART", 40, "2026-11-28")
        s = self.book.summary()
        self.assertEqual(s["offers"], 1)
        self.assertEqual(s["ordered"], 1)
        self.assertEqual(s["unpaid"], 1)         # SHARMA unpaid
        self.assertEqual(s["undelivered"], 1)    # SHARMA received 0/60


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "book.json")

    def test_roundtrip(self):
        book = book_with_offer()
        book.join("POOL-1", "SHARMA STORES", 100)
        book.close_order("POOL-1", "2026-11-24")
        book.record_payment("POOL-1", "SHARMA STORES", "2026-11-30")
        book.save(self.path)
        loaded = pb.Book.load(self.path)
        offer = loaded.get_offer("POOL-1")
        self.assertTrue(offer["participants"][0]["paid"])
        self.assertEqual(offer["ordered"]["date"], "2026-11-24")

    def test_load_missing_file_gives_empty_book(self):
        book = pb.Book.load(os.path.join(self.tmp.name, "nope.json"))
        self.assertEqual(len(book.offers), 0)

    def test_corrupt_file_raises_user_error(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ nope")
        with self.assertRaises(pb.PoolBookError):
            pb.Book.load(self.path)

    def test_save_is_atomic_replace(self):
        book = book_with_offer()
        book.save(self.path)
        self.assertTrue(os.path.exists(self.path))
        leftovers = [n for n in os.listdir(self.tmp.name) if n != "book.json"]
        self.assertEqual(leftovers, [])
        with open(self.path, encoding="utf-8") as fh:
            json.load(fh)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "book.json")

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = pb.main(["--db", self.db] + list(argv))
        return code, out.getvalue()

    def seed(self):
        self.run_cli("offer", "POOL-101", "AMULYA", "ghee 1L",
                     "--unit", "420", "--moq", "100", "--delivery", "3000",
                     "--distributor-unit", "520")
        self.run_cli("join", "POOL-101", "SHARMA STORES", "50")
        self.run_cli("join", "POOL-101", "GUPTA MART", "30")
        self.run_cli("join", "POOL-101", "KHAN PROVISIONS", "20")

    def test_full_flow_and_outputs(self):
        code, out = self.run_cli("offer", "POOL-101", "AMULYA", "ghee 1L",
                                 "--unit", "420", "--moq", "100",
                                 "--delivery", "3000",
                                 "--distributor-unit", "520")
        self.assertEqual(code, 0)
        self.assertIn("420.00/unit, MOQ 100, delivery 3,000.00", out)
        self.assertIn("distributor 520.00/unit", out)

        code, out = self.run_cli("join", "POOL-101", "SHARMA STORES", "50")
        self.assertIn("pool at 50 of 100, 50 units short of MOQ", out)
        self.seed_rest = self.run_cli("join", "POOL-101", "GUPTA MART", "30")
        code, out = self.run_cli("join", "POOL-101", "KHAN PROVISIONS",
                                 "20")
        self.assertIn("MOQ met", out)

        code, out = self.run_cli("pool", "POOL-101")
        self.assertEqual(code, 0)
        self.assertIn("MOQ met, ready to order", out)
        self.assertIn("SHARMA STORES", out)
        self.assertIn("50 units", out)
        self.assertIn("goods    21,000.00 + delivery  1,500.00 =    22,500.00", out)
        self.assertIn("saves 3,500.00 vs distributor", out)
        self.assertIn("KHAN PROVISIONS", out)
        self.assertIn("20 units", out)
        self.assertIn("=     9,000.00", out)

        code, out = self.run_cli("pools")
        self.assertIn("POOL-101", out)
        self.assertIn("100/100 (MOQ met), 3 retailer(s)", out)

        code, out = self.run_cli("close", "POOL-101", "2026-11-24")
        self.assertIn("ordered offer POOL-101 on 2026-11-24", out)

        code, out = self.run_cli("receive", "POOL-101", "GUPTA MART", "30",
                                 "2026-11-28")
        self.assertIn("complete", out)

        code, out = self.run_cli("pay", "POOL-101", "GUPTA MART",
                                 "2026-11-30")
        self.assertIn("recorded payment from GUPTA MART", out)

        code, out = self.run_cli("pool", "POOL-101")
        self.assertIn("ordered on 2026-11-24", out)
        self.assertIn("[received 30/30, paid]", out)
        self.assertIn("[received 0/50]", out)

        code, out = self.run_cli("summary")
        self.assertIn("offers: 1 (open 0, ordered 1)", out)
        self.assertIn("unpaid participants: 2", out)

    def test_cli_close_below_moq_exit_code(self):
        self.run_cli("offer", "POOL-1", "B", "p", "--unit", "10",
                     "--moq", "100")
        self.run_cli("join", "POOL-1", "SHOP A", "50")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = pb.main(["--db", self.db, "close", "POOL-1",
                            "2026-11-24"])
        self.assertEqual(code, 2)
        self.assertIn("50 units short of the MOQ 100", err.getvalue())

    def test_cli_export(self):
        self.seed()
        self.run_cli("close", "POOL-101", "2026-11-24")
        csv_path = os.path.join(self.tmp.name, "out.csv")
        code, out = self.run_cli("export", csv_path)
        self.assertEqual(code, 0)
        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0][0], "offer")
        self.assertEqual(len(rows), 4)
        by_retailer = {r[3]: r for r in rows[1:]}
        sharma = by_retailer["SHARMA STORES"]
        self.assertEqual(sharma[5], "21,000.00")   # goods
        self.assertEqual(sharma[6], "1,500.00")    # delivery share
        self.assertEqual(sharma[7], "22,500.00")   # total
        self.assertEqual(sharma[9], "3,500.00")    # saving
        self.assertEqual(sharma[12], "2026-11-24") # ordered on
        khan = by_retailer["KHAN PROVISIONS"]
        self.assertEqual(khan[6], "600.00")        # 20% of delivery


if __name__ == "__main__":
    unittest.main()
