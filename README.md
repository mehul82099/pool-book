# pool-book

A zero-dependency command-line pooled group-order book for small
retailers. One small shop rarely meets a brand's minimum order quantity,
so it buys from a distributor at a margin. pool-book coordinates the
pooled order: record the brand's offer (unit price, MOQ, delivery
charge, and the distributor price for comparison), let retailers join
with committed quantities, and the book answers: has the pool reached
MOQ, what does each participant owe (goods plus an exact proportional
delivery share), what do they save versus the distributor price, who has
received their goods, and who has paid.

Single Python file, standard library only (Python 3.9+). Fully offline;
all data lives in one local JSON file you own.

## Why this exists

On Razorpay's public "Fix My Itch" list, one entry asks: "Why are small
retailers forced into distributor margins without direct brand access?"
(page fetched and entry re-verified on 2026-11-24;
https://razorpay.com/m/fix-my-itch/). That list is curated by Razorpay -
it shows the problem was submitted and published, not how common it is or
that a tool like this is in demand.

Pooling demand is how small retailers reach a brand's MOQ together. The
coordination itself is the fragile part - who committed how much, did the
pool reach MOQ, who owes what including the delivery split, who received
their goods, who has paid. That record usually lives in a chat group and
one person's memory. This book keeps it instead.

## What it does

- Offers: record a brand offer - product, brand unit price, MOQ, total
  delivery charge, the distributor price for comparison, validity.
- Join: retailers commit quantities. No joining after the order is
  placed; one entry per retailer per offer.
- Status (`pool`, `pools`): committed vs MOQ, and once the pool is
  forming, each retailer's split: goods cost + delivery share = total,
  with the saving versus the distributor price.
- Exact splits: the delivery charge is divided proportionally by
  quantity using largest-remainder rounding, so shares are whole paise
  and always sum to the charge exactly - no "who covers the odd rupee".
- Order (`close`): place the order only when MOQ is met; re-closing is
  rejected.
- Fulfilment: `receive` records goods per retailer (never more than
  committed), `pay` records each retailer's payment (never twice).
- Export (`export`): all pools to CSV.
- Amounts are stored as integer paise and printed with Indian digit
  grouping (22,500.00).

## Install and run

Requires Python 3.9 or newer. Nothing else.

```sh
python3 pool_book.py --help
```

Data is stored in `pool_book.json` next to the script (override with
`--db PATH`).

## Example

```console
$ python3 pool_book.py offer POOL-101 AMULYA "ghee 1L" --unit 420 --moq 100 --delivery 3000 --distributor-unit 520
recorded offer POOL-101: AMULYA ghee 1L at 420.00/unit, MOQ 100, delivery 3,000.00, distributor 520.00/unit

$ python3 pool_book.py join POOL-101 "SHARMA STORES" 50
SHARMA STORES joined POOL-101 with 50 units (pool at 50 of 100, 50 units short of MOQ)

$ python3 pool_book.py join POOL-101 "GUPTA MART" 30
GUPTA MART joined POOL-101 with 30 units (pool at 80 of 100, 20 units short of MOQ)

$ python3 pool_book.py join POOL-101 "KHAN PROVISIONS" 20
KHAN PROVISIONS joined POOL-101 with 20 units (pool at 100 of 100, MOQ met)

$ python3 pool_book.py pool POOL-101
POOL-101: AMULYA ghee 1L at 420.00/unit, MOQ 100, delivery 3,000.00 - MOQ met, ready to order
  SHARMA STORES            50 units  goods    21,000.00 + delivery  1,500.00 =    22,500.00  saves 3,500.00 vs distributor
  GUPTA MART               30 units  goods    12,600.00 + delivery    900.00 =    13,500.00  saves 2,100.00 vs distributor
  KHAN PROVISIONS          20 units  goods     8,400.00 + delivery    600.00 =     9,000.00  saves 1,400.00 vs distributor

$ python3 pool_book.py close POOL-101 2026-11-24
ordered offer POOL-101 on 2026-11-24: 100 units across 3 retailer(s)

$ python3 pool_book.py receive POOL-101 "GUPTA MART" 30 2026-11-28
recorded 30 units received by GUPTA MART on 2026-11-28 (complete)

$ python3 pool_book.py pay POOL-101 "GUPTA MART" 2026-11-30
recorded payment from GUPTA MART for POOL-101 on 2026-11-30

$ python3 pool_book.py pool POOL-101
POOL-101: AMULYA ghee 1L at 420.00/unit, MOQ 100, delivery 3,000.00 - ordered on 2026-11-24
  SHARMA STORES            50 units  goods    21,000.00 + delivery  1,500.00 =    22,500.00  saves 3,500.00 vs distributor  [received 0/50]
  GUPTA MART               30 units  goods    12,600.00 + delivery    900.00 =    13,500.00  saves 2,100.00 vs distributor  [received 30/30, paid]
  KHAN PROVISIONS          20 units  goods     8,400.00 + delivery    600.00 =     9,000.00  saves 1,400.00 vs distributor  [received 0/20]

$ python3 pool_book.py summary
offers: 1 (open 0, ordered 1), unpaid participants: 2, incomplete deliveries: 2
```

(Every line above is real output from a run of this script, with numbers
chosen so the math is easy to check: the 3,000.00 delivery splits
1,500/900/600 by quantity, and each retailer's saving is their committed
quantity x (520 - 420) minus their delivery share.)

## What else is out there (as of 2026-11-24)

Group-buying and B2B supply platforms exist and do much more - Kirana
Club and StoreFiller, for example, aggregate retailer demand or supply
kiranas directly. This repo does not claim to be the only tool in this
space, and it is not a marketplace. Its scope is deliberately smaller:
an offline, zero-install record for a group of retailers who have
already agreed to pool an order - usually coordinated in a chat group -
so commitments, exact money splits, deliveries and payments live in a
file the coordinator owns. If you want discovery or managed supply, use
a platform.

## Limits

- No payments move through it: it records who owes and who paid; it does
  not collect or transfer money.
- No discovery: it does not find brands, other retailers or better
  prices. You bring the offer; it keeps the books.
- Savings are arithmetic against the distributor price you enter; real
  savings also depend on quality, credit terms and reliability, which are
  not modeled.
- The delivery split is proportional by quantity (largest-remainder
  rounding). Other split rules (equal shares, by weight) are not
  implemented.
- Single user, single file. No multi-user access, no sync.
- Durability: saves are atomic (write to a temp file, then replace), which
  protects against interruption mid-save, not against deleting the file.
  Back up the JSON file like any other business record.

## Tests

```sh
python3 -m unittest test_pool_book -v
```

32 tests cover money and count parsing, Indian formatting, the
largest-remainder delivery split (exact sums, proportionality,
deterministic ties), offer/join validation, MOQ gating, the
join -> close -> receive/pay lifecycle (out-of-order and double events
rejected), per-participant totals and savings, CSV export, atomic
save/load roundtrip, and the CLI end to end. CI runs the suite on Ubuntu
and Windows against Python 3.9 and 3.13.

## License

MIT. See [LICENSE](LICENSE).
