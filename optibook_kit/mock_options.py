"""Practice exchange #3: one stock (NVDA) plus calls and puts on it, with the optibook-style API.
get_instruments() returns objects with option fields, like the real client is expected to
(verify the field names on the day: option_kind, strike, expiry, base_instrument_id).

Market model:
- Stock follows a random walk. Each option's book is centred on its Black-Scholes value at the TRUE
  volatility, plus a mispricing that wanders and reverts. Option spreads are wide.
- Some takers are informed about the stock's next move and trade options in that direction.
"""
import math
import datetime as dt
from dataclasses import dataclass
from mock_optibook import Exchange as _Base, _Order, _r, TICK


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S, K, T, sigma, kind, r=0.0):
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if kind == "call" else max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if kind == "call":
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


@dataclass
class Instrument:
    instrument_id: str
    instrument_type: str            # "stock" or "option"
    base_instrument_id: str = None
    option_kind: str = None         # "call" / "put"
    strike: float = None
    expiry: dt.datetime = None


STOCK = "NVDA"
STRIKES = (120, 125, 130, 135, 140)
EXPIRY = dt.datetime(2026, 12, 18, 16, 0)


class Exchange(_Base):
    INSTRUMENTS = {STOCK: dict(half=0.15, depth=300, p_take=0.7, size=(5, 40))}
    for _k in STRIKES:
        for _kind in ("C", "P"):
            INSTRUMENTS[f"{STOCK}-{_k}{_kind}"] = dict(half=0.3, depth=20, p_take=0.25, size=(2, 10))

    def __init__(self, seed=0, position_limit=100, informed=0.3, true_vol=0.30, start=None):
        super().__init__(seed=seed, position_limit=position_limit, informed=informed)
        self.S, self.vol = 130.0, true_vol
        self.now = start or dt.datetime(2026, 11, 12, 17, 0)
        self.meta = {STOCK: Instrument(STOCK, "stock")}
        for k in STRIKES:
            for kind, name in (("call", "C"), ("put", "P")):
                iid = f"{STOCK}-{k}{name}"
                self.meta[iid] = Instrument(iid, "option", STOCK, kind, float(k), EXPIRY)
        self.mis = {i: 0.0 for i in self.INSTRUMENTS}
        self.next_dS = self.rng.gauss(0, 0.12)

    def get_instruments(self):
        return dict(self.meta)

    def current_time(self):
        return self.now

    def true_value(self, iid):
        m = self.meta[iid]
        if m.instrument_type == "stock":
            return self.S
        T = (m.expiry - self.now).total_seconds() / (365 * 24 * 3600)
        return bs_price(self.S, m.strike, T, self.vol, m.option_kind)

    def advance(self, _seconds=None):
        self.t += 1
        dS = self.next_dS
        for iid, cfg in self.INSTRUMENTS.items():
            if self.rng.random() < cfg["p_take"]:
                m = self.meta[iid]
                up = dS if (m.instrument_type == "stock" or m.option_kind == "call") else -dS
                if self.rng.random() < self.informed and abs(dS) > 0.03:
                    side = "buy" if up > 0 else "sell"
                else:
                    side = self.rng.choice(["buy", "sell"])
                self._taker(iid, side, self.rng.randint(*cfg["size"]))
        self.S += dS
        self.next_dS = self.rng.gauss(0, 0.12)
        self.now += dt.timedelta(seconds=1)
        for iid, cfg in self.INSTRUMENTS.items():
            self.mis[iid] = 0.9 * self.mis[iid] + self.rng.gauss(0, 0.25 * cfg["half"])
        self._refresh_background()

    def get_pnl(self):
        pnl = self.cash
        for i, p in self.pos.items():
            b = self.get_last_price_book(i)
            mid = (b.bids[0].price + b.asks[0].price) / 2 if b.bids and b.asks else self.true_value(i)
            pnl += p * mid
        return pnl

    def connect(self):
        self._refresh_background()

    def _refresh_background(self):
        if not hasattr(self, "meta"):
            return
        for iid, cfg in self.INSTRUMENTS.items():
            fv = self.true_value(iid) + self.mis[iid]
            mine = [o for o in self.books[iid] if o.owner == "me"]
            bg = []
            for lvl in range(3):
                bid = _r(fv - cfg["half"] - lvl * TICK)
                if bid > 0:
                    bg.append(_Order(0, "bg", bid, cfg["depth"], "bid"))
                bg.append(_Order(0, "bg", _r(max(fv, 0.1) + cfg["half"] + lvl * TICK), cfg["depth"], "ask"))
            for o in mine:
                for b in bg:
                    crossed = (o.side == "bid" and b.side == "ask" and b.price <= o.price) or \
                              (o.side == "ask" and b.side == "bid" and b.price >= o.price)
                    if crossed and o.volume and b.volume:
                        q = min(o.volume, b.volume)
                        o.volume -= q
                        b.volume -= q
                        self._fill_me(iid, o.price, q, o.side)
            self.books[iid] = [o for o in mine if o.volume > 0] + [b for b in bg if b.volume > 0]
