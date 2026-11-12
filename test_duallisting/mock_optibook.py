"""Local stand-in for Optibook, shaped like the public optibook client:
    e = Exchange(); e.connect()
    book = e.get_last_price_book("PHILIPS_A")   # book.bids / book.asks -> [PriceVolume(price, volume), ...]
    e.insert_order(instrument_id, price=..., volume=..., side="bid"|"ask", order_type="limit"|"ioc")
    e.delete_orders(instrument_id); e.get_positions(); e.poll_new_trades(instrument_id); e.get_pnl()
On the day: replace `from mock_optibook import Exchange` with the real import from their tutorial,
and pass time.sleep instead of e.advance as the sleep function. Verify method names against their docs.

Market model: one company, two listings. A is liquid (tight, deep), B is illiquid (wide, thin) and
its price wanders away from A and snaps back. Random takers hit the best prices each tick; some of
them are 'informed' (they trade in the direction of the next price move) so careless quoting loses.
"""
import random
from dataclasses import dataclass

TICK = 0.1


@dataclass
class PriceVolume:
    price: float
    volume: int


@dataclass
class PriceBook:
    instrument_id: str
    bids: list
    asks: list


@dataclass
class Trade:
    instrument_id: str
    price: float
    volume: int
    side: str  # side of OUR order: "bid" (we bought) or "ask" (we sold)


@dataclass
class _Order:
    oid: int
    owner: str
    price: float
    volume: int
    side: str


def _r(x):
    return round(round(x / TICK) * TICK, 1)


class Exchange:
    INSTRUMENTS = {
        # half-spread, depth per level, taker prob per tick, taker size range
        "PHILIPS_A": dict(half=0.1, depth=200, p_take=0.6, size=(5, 40)),
        "PHILIPS_B": dict(half=0.3, depth=25, p_take=0.35, size=(5, 25)),
    }

    def __init__(self, seed=0, position_limit=100, informed=0.3):
        self.rng = random.Random(seed)
        self.limit, self.informed = position_limit, informed
        self.fair = 100.0
        self.offset = 0.0                      # B's dislocation vs A (mean-reverting)
        self.next_move = 0.0
        self.books = {i: [] for i in self.INSTRUMENTS}
        self.pos = {i: 0 for i in self.INSTRUMENTS}
        self.cash = 0.0
        self.trades = {i: [] for i in self.INSTRUMENTS}
        self._oid, self.t = 0, 0
        self.volume_traded = 0

    # ---- public API (optibook-style) ----
    def connect(self):
        self._refresh_background()

    def is_connected(self):
        return True

    def get_instruments(self):
        return list(self.INSTRUMENTS)

    def get_last_price_book(self, instrument_id):
        bids, asks = {}, {}
        for o in self.books[instrument_id]:
            d = bids if o.side == "bid" else asks
            d[o.price] = d.get(o.price, 0) + o.volume
        return PriceBook(instrument_id,
                         [PriceVolume(p, v) for p, v in sorted(bids.items(), reverse=True)],
                         [PriceVolume(p, v) for p, v in sorted(asks.items())])

    def get_positions(self):
        return dict(self.pos)

    def get_pnl(self):
        a = self.get_last_price_book("PHILIPS_A")
        mid = (a.bids[0].price + a.asks[0].price) / 2
        return self.cash + sum(self.pos.values()) * mid

    def get_outstanding_orders(self, instrument_id):
        return {o.oid: o for o in self.books[instrument_id] if o.owner == "me"}

    def delete_orders(self, instrument_id):
        self.books[instrument_id] = [o for o in self.books[instrument_id] if o.owner != "me"]

    def poll_new_trades(self, instrument_id):
        out, self.trades[instrument_id] = self.trades[instrument_id], []
        return out

    def insert_order(self, instrument_id, *, price, volume, side, order_type="limit"):
        price, volume = _r(price), int(volume)
        signed = volume if side == "bid" else -volume
        if volume <= 0 or abs(self.pos[instrument_id] + signed) > self.limit:
            return None  # real Optibook rejects orders that could breach the limit
        # match against the opposite side (background liquidity only; no self-trade)
        opp = sorted((o for o in self.books[instrument_id] if o.side != side and o.owner != "me"),
                     key=lambda o: o.price if side == "bid" else -o.price)
        left = volume
        for o in opp:
            if left == 0 or (side == "bid" and o.price > price) or (side == "ask" and o.price < price):
                break
            q = min(left, o.volume)
            o.volume -= q
            left -= q
            self._fill_me(instrument_id, o.price, q, side)
        self.books[instrument_id] = [o for o in self.books[instrument_id] if o.volume > 0]
        if left and order_type == "limit":
            self._oid += 1
            self.books[instrument_id].append(_Order(self._oid, "me", price, left, side))
        return self._oid

    # ---- simulation (call this where the real bot would sleep) ----
    def advance(self, _seconds=None):
        self.t += 1
        move = self.next_move
        self.next_move = self.rng.gauss(0, 0.08)
        # takers trade BEFORE the price moves; informed ones know the direction
        for inst, cfg in self.INSTRUMENTS.items():
            if self.rng.random() < cfg["p_take"]:
                if self.rng.random() < self.informed and abs(move) > 0.02:
                    side = "buy" if move > 0 else "sell"
                else:
                    side = self.rng.choice(["buy", "sell"])
                self._taker(inst, side, self.rng.randint(*cfg["size"]))
        self.fair += move
        self.offset = 0.97 * self.offset + self.rng.gauss(0, 0.06)
        self._refresh_background()

    def _taker(self, inst, side, size):
        # hits resting orders best-price-first; at equal price, background has priority (conservative)
        target = "ask" if side == "buy" else "bid"
        cands = [o for o in self.books[inst] if o.side == target]
        cands.sort(key=lambda o: (o.price if target == "ask" else -o.price, o.owner == "me"))
        for o in cands:
            if size == 0:
                break
            q = min(size, o.volume)
            o.volume -= q
            size -= q
            if o.owner == "me":
                self._fill_me(inst, o.price, q, o.side)
        self.books[inst] = [o for o in self.books[inst] if o.volume > 0]

    def _fill_me(self, inst, price, q, side):
        s = q if side == "bid" else -q
        self.pos[inst] += s
        self.cash -= s * price
        self.volume_traded += q
        self.trades[inst].append(Trade(inst, price, q, side))

    def _refresh_background(self):
        for inst, cfg in self.INSTRUMENTS.items():
            fv = self.fair + (self.offset if inst == "PHILIPS_B" else 0)
            mine = [o for o in self.books[inst] if o.owner == "me"]
            bg = []
            for lvl in range(3):
                bg.append(_Order(0, "bg", _r(fv - cfg["half"] - lvl * TICK), cfg["depth"], "bid"))
                bg.append(_Order(0, "bg", _r(fv + cfg["half"] + lvl * TICK), cfg["depth"], "ask"))
            # our resting orders that are now crossed by the new background get filled
            for o in mine:
                for b in bg:
                    crossed = (o.side == "bid" and b.side == "ask" and b.price <= o.price) or \
                              (o.side == "ask" and b.side == "bid" and b.price >= o.price)
                    if crossed and o.volume:
                        q = min(o.volume, b.volume)
                        o.volume -= q
                        b.volume -= q
                        self._fill_me(inst, o.price, q, o.side)
            self.books[inst] = [o for o in mine if o.volume > 0] + [b for b in bg if b.volume > 0]
