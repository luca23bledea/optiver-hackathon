"""Practice exchange #2: several related stocks, like the real Optibook screen (NVDA, MU, AMD, CRWV, NBIS).
Same optibook-style API as mock_optibook.py (it reuses its matching engine).

Market model:
- All stocks share a common 'AI sector' factor, scaled by each stock's beta.
- CRWV is tied to NVDA (cointegrated): its fair price follows NVDA's with elasticity 1.3, plus a
  small mean-reverting gap. This mimics the 'coointegration-quoter' hint.
- Each book's mid wanders a bit around fair (mispricing that reverts), more on illiquid names.
- Some takers are informed: they trade in the direction of the next fair move.
"""
import math
from mock_optibook import Exchange as _Base, _Order, _r, TICK


class Exchange(_Base):
    INSTRUMENTS = {
        "NVDA": dict(half=0.15, depth=300, p_take=0.7, size=(5, 40), px=130.75, beta=1.0),
        "MU":   dict(half=0.15, depth=300, p_take=0.6, size=(5, 40), px=77.95, beta=0.9),
        "AMD":  dict(half=0.2, depth=300, p_take=0.6, size=(5, 40), px=162.7, beta=1.1),
        "NBIS": dict(half=0.4, depth=40, p_take=0.35, size=(5, 20), px=91.1, beta=1.2),
        "CRWV": dict(half=0.6, depth=30, p_take=0.35, size=(5, 20), px=143.25, tied=("NVDA", 1.3)),
    }

    def __init__(self, seed=0, position_limit=100, informed=0.3, factor_vol=0.0008, idio_vol=0.0004):
        super().__init__(seed=seed, position_limit=position_limit, informed=informed)
        self.fvol, self.ivol = factor_vol, idio_vol
        self.logf = {i: math.log(c["px"]) for i, c in self.INSTRUMENTS.items()}
        self.log0 = dict(self.logf)
        self.mis = {i: 0.0 for i in self.INSTRUMENTS}
        self.gap = 0.0
        self.next_logf = self._evolve()

    def fair_price(self, inst):
        return math.exp(self.logf[inst])

    def _evolve(self):
        f = self.rng.gauss(0, self.fvol)
        nxt = {}
        for i, c in self.INSTRUMENTS.items():
            if "tied" not in c:
                nxt[i] = self.logf[i] + c["beta"] * f + self.rng.gauss(0, self.ivol)
        self.gap = 0.98 * self.gap + self.rng.gauss(0, 0.0004)
        for i, c in self.INSTRUMENTS.items():
            if "tied" in c:
                ref, b = c["tied"]
                nxt[i] = self.log0[i] + b * (nxt[ref] - self.log0[ref]) + self.gap
        return nxt

    def advance(self, _seconds=None):
        self.t += 1
        for inst, cfg in self.INSTRUMENTS.items():
            if self.rng.random() < cfg["p_take"]:
                up = self.next_logf[inst] - self.logf[inst]
                if self.rng.random() < self.informed and abs(up) > 0.0003:
                    side = "buy" if up > 0 else "sell"
                else:
                    side = self.rng.choice(["buy", "sell"])
                self._taker(inst, side, self.rng.randint(*cfg["size"]))
        self.logf = self.next_logf
        self.next_logf = self._evolve()
        for i, c in self.INSTRUMENTS.items():
            self.mis[i] = 0.9 * self.mis[i] + self.rng.gauss(0, 0.3 * c["half"])
        self._refresh_background()

    def get_pnl(self):
        pnl = self.cash
        for i, p in self.pos.items():
            b = self.get_last_price_book(i)
            pnl += p * (b.bids[0].price + b.asks[0].price) / 2
        return pnl

    def _refresh_background(self):
        if not hasattr(self, "logf"):
            return
        for inst, cfg in self.INSTRUMENTS.items():
            fv = self.fair_price(inst) + self.mis[inst]
            mine = [o for o in self.books[inst] if o.owner == "me"]
            bg = []
            for lvl in range(3):
                bg.append(_Order(0, "bg", _r(fv - cfg["half"] - lvl * TICK), cfg["depth"], "bid"))
                bg.append(_Order(0, "bg", _r(fv + cfg["half"] + lvl * TICK), cfg["depth"], "ask"))
            for o in mine:
                for b in bg:
                    crossed = (o.side == "bid" and b.side == "ask" and b.price <= o.price) or \
                              (o.side == "ask" and b.side == "bid" and b.price >= o.price)
                    if crossed and o.volume and b.volume:
                        q = min(o.volume, b.volume)
                        o.volume -= q
                        b.volume -= q
                        self._fill_me(inst, o.price, q, o.side)
            self.books[inst] = [o for o in mine if o.volume > 0] + [b for b in bg if b.volume > 0]

    def connect(self):
        self._refresh_background()
