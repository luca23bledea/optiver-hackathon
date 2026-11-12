"""Options market maker: price every option with Black-Scholes, quote around that value,
take obvious mispricings, and delta-hedge the whole book with the stock.

Day-of usage (verify the import and the option field names in their example code):
    from optibook.synchronous_client import Exchange
    import time, options_bot
    e = Exchange(); e.connect()
    options_bot.run(e, sleep=time.sleep, interval=0.2, cfg=options_bot.Config(stock="NVDA", vol=0.30))
"""
import math
import datetime as dt
from dataclasses import dataclass, replace


# ---------- Black-Scholes (no scipy needed) ----------
def _N(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _n(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs(S, K, T, sigma, kind, r=0.0):
    """Returns (price, delta, vega). vega = price change per +1.00 (100 vol points) of sigma."""
    if T <= 0 or sigma <= 0:
        intrinsic = max(0.0, S - K) if kind == "call" else max(0.0, K - S)
        delta = (1.0 if S > K else 0.0) if kind == "call" else (-1.0 if S < K else 0.0)
        return intrinsic, delta, 0.0
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / sq
    d2 = d1 - sq
    vega = S * _n(d1) * math.sqrt(T)
    if kind == "call":
        return S * _N(d1) - K * math.exp(-r * T) * _N(d2), _N(d1), vega
    return K * math.exp(-r * T) * _N(-d2) - S * _N(-d1), _N(d1) - 1, vega


def implied_vol(price, S, K, T, kind, r=0.0, lo=0.01, hi=3.0):
    """Find the sigma that makes Black-Scholes match an observed price (bisection)."""
    if T <= 0 or price <= 0:
        return None
    if price <= bs(S, K, T, lo, kind, r)[0] or price >= bs(S, K, T, hi, kind, r)[0]:
        return None
    for _ in range(50):
        mid = (lo + hi) / 2
        if bs(S, K, T, mid, kind, r)[0] < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass
class Config:
    stock: str = "NVDA"
    vol: float = 0.30            # starting volatility estimate; THE key input
    auto_vol: bool = True        # re-estimate vol from the market's option prices (implied vol)
    vol_smoothing: float = 0.05  # how fast the estimate follows the market (0..1)
    r: float = 0.0
    edge: float = 0.2            # quote this far from fair value
    take_edge: float = 0.25      # trade immediately if a book is this far through fair
    skew: float = 0.003          # shift an option's quotes per lot of position in it
    quote_size: int = 5
    max_option_pos: int = 50     # per option
    max_stock_pos: int = 90
    delta_threshold: float = 8   # hedge once |total delta| exceeds this many shares
    hedge_on: bool = True
    max_vega: float = 300        # stop adding to the side that grows |total vega| beyond this
    tick: float = 0.1


def best(book):
    if not book.bids or not book.asks:
        return None
    return book.bids[0].price, book.bids[0].volume, book.asks[0].price, book.asks[0].volume


def rnd(x, tick):
    return round(round(x / tick) * tick, 1)


def _kind(inst):
    k = getattr(inst, "option_kind", None)
    k = str(getattr(k, "name", k)).lower()  # handles enums like OptionKind.CALL
    return "call" if "call" in k else "put"


class OptionsBot:
    def __init__(self, e, cfg: Config = Config(), now=None):
        self.e, self.c = e, replace(cfg)  # own copy: auto_vol updates c.vol
        self.now = now or dt.datetime.now
        insts = e.get_instruments()
        self.options = {iid: i for iid, i in insts.items()
                        if getattr(i, "base_instrument_id", None) == cfg.stock
                        and getattr(i, "strike", None) is not None}
        self.greeks = {}

    def _years_left(self, inst):
        exp = inst.expiry
        return max((exp - self.now()).total_seconds(), 0) / (365 * 24 * 3600)

    def step(self):
        c, e = self.c, self.e
        sb = best(e.get_last_price_book(c.stock))
        if not sb:
            return
        S = (sb[0] + sb[2]) / 2
        pos = e.get_positions()

        # 0) learn volatility from option mids (median implied vol of near-the-money options)
        if c.auto_vol:
            ivs = []
            for iid, inst in self.options.items():
                if abs(inst.strike - S) / S > 0.06:
                    continue
                b = best(e.get_last_price_book(iid))
                if b:
                    iv = implied_vol((b[0] + b[2]) / 2, S, inst.strike, self._years_left(inst), _kind(inst), c.r)
                    if iv:
                        ivs.append(iv)
            if ivs:
                ivs.sort()
                c.vol += c.vol_smoothing * (ivs[len(ivs) // 2] - c.vol)

        # 1) value every option, and our total delta / vega
        total_delta, total_vega = pos.get(c.stock, 0), 0.0
        for iid, inst in self.options.items():
            price, delta, vega = bs(S, inst.strike, self._years_left(inst), c.vol, _kind(inst), c.r)
            self.greeks[iid] = (price, delta, vega)
            total_delta += pos.get(iid, 0) * delta
            total_vega += pos.get(iid, 0) * vega / 100  # per 1 vol point

        # 2) per option: take mispricing, then requote
        for iid in self.options:
            fair, delta, vega = self.greeks[iid]
            book = e.get_last_price_book(iid)
            b = best(book)
            p = e.get_positions().get(iid, 0)
            if b:
                bid, bidv, ask, askv = b
                if bid - fair >= c.take_edge and p > -c.max_option_pos:
                    e.insert_order(iid, price=bid, volume=min(bidv, c.quote_size, c.max_option_pos + p),
                                   side="ask", order_type="ioc")
                elif fair - ask >= c.take_edge and p < c.max_option_pos:
                    e.insert_order(iid, price=ask, volume=min(askv, c.quote_size, c.max_option_pos - p),
                                   side="bid", order_type="ioc")
            e.delete_orders(iid)
            p = e.get_positions().get(iid, 0)
            center = fair - c.skew * p
            bid_px, ask_px = rnd(center - c.edge, c.tick), rnd(center + c.edge, c.tick)
            # vega limit: if already long too much vega, stop buying options; if short, stop selling
            can_buy = total_vega < c.max_vega
            can_sell = total_vega > -c.max_vega
            bv, av = min(c.quote_size, c.max_option_pos - p), min(c.quote_size, c.max_option_pos + p)
            if can_buy and bv > 0 and bid_px > 0:
                e.insert_order(iid, price=bid_px, volume=bv, side="bid", order_type="limit")
            if can_sell and av > 0:
                e.insert_order(iid, price=max(ask_px, c.tick), volume=av, side="ask", order_type="limit")

        # 3) delta hedge with the stock
        if c.hedge_on:
            pos = e.get_positions()
            d = pos.get(c.stock, 0) + sum(pos.get(i, 0) * self.greeks[i][1] for i in self.options)
            if abs(d) > c.delta_threshold:
                sp = pos.get(c.stock, 0)
                if d > 0:
                    v = min(round(d), sb[1], c.max_stock_pos + sp)
                    if v > 0:
                        e.insert_order(c.stock, price=sb[0], volume=v, side="ask", order_type="ioc")
                else:
                    v = min(round(-d), sb[3], c.max_stock_pos - sp)
                    if v > 0:
                        e.insert_order(c.stock, price=sb[2], volume=v, side="bid", order_type="ioc")

    def flatten(self):
        for iid in list(self.options) + [self.c.stock]:
            self.e.delete_orders(iid)
            p = self.e.get_positions().get(iid, 0)
            b = best(self.e.get_last_price_book(iid))
            if p and b:
                side, px = ("ask", b[0]) if p > 0 else ("bid", b[2])
                self.e.insert_order(iid, price=px, volume=abs(p), side=side, order_type="ioc")

    def summary(self):
        pos = self.e.get_positions()
        d = pos.get(self.c.stock, 0) + sum(pos.get(i, 0) * g[1] for i, g in self.greeks.items())
        v = sum(pos.get(i, 0) * g[2] / 100 for i, g in self.greeks.items())
        return f"pnl {self.e.get_pnl():8.1f}  delta {d:6.1f}  vega {v:6.1f}  vol {self.c.vol:.3f}"


def run(e, sleep, interval=0.2, steps=None, cfg: Config = Config(), now=None, verbose=True):
    bot = OptionsBot(e, cfg, now)
    i = 0
    try:
        while steps is None or i < steps:
            bot.step()
            sleep(interval)
            i += 1
            if verbose and i % 500 == 0:
                print(f"step {i}: {bot.summary()}")
    finally:
        bot.flatten()
    return bot


if __name__ == "__main__":
    import mock_options as M
    e = M.Exchange(seed=1)
    e.connect()
    run(e, sleep=e.advance, steps=3000, now=e.current_time)
    print(f"final pnl {e.get_pnl():.1f}, traded {e.volume_traded} lots")
