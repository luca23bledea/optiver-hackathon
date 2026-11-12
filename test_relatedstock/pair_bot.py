"""Pair bot: make markets on an illiquid stock X (e.g. CRWV) and hedge with a related liquid stock Y
(e.g. NVDA), using a hedge ratio it learns from live prices. Generalises bot.py to two DIFFERENT
companies that move together. Pick X and Y with pair_finder.py first.

Day-of usage (verify the import in their example code):
    from optibook.synchronous_client import Exchange
    import time, pair_bot
    e = Exchange(); e.connect()
    pair_bot.run(e, sleep=time.sleep, interval=0.2, cfg=pair_bot.Config(quote="CRWV", hedge="NVDA"))
"""
import math
from collections import deque
from dataclasses import dataclass


@dataclass
class Config:
    quote: str = "CRWV"          # illiquid stock we make markets in
    hedge: str = "NVDA"          # liquid stock we hedge with
    window: int = 300            # how many recent samples the hedge ratio is learned from
    warmup: int = 100            # samples to collect before trading
    model_weight: float = 0.5    # 0 = fair is X's own mid, 1 = fair is what Y implies X should be
    edge: float = 0.4            # quote this far from fair
    take_edge: float = 0.4       # take X outright if its book is this far through fair
    skew: float = 0.005          # shift X quotes per lot of X position
    quote_size: int = 10
    max_pos: int = 80
    hedge_threshold: int = 15    # tolerate this much hedge error (in Y lots) before trading Y
    hedge_on: bool = True
    tick: float = 0.1


def best(book):
    if not book.bids or not book.asks:
        return None
    return book.bids[0].price, book.bids[0].volume, book.asks[0].price, book.asks[0].volume


def rnd(x, tick):
    return round(round(x / tick) * tick, 1)


class PairBot:
    def __init__(self, e, cfg: Config = Config()):
        self.e, self.c = e, cfg
        self.hist = deque(maxlen=cfg.window)
        self.ratio, self.fair = None, None

    def _learn(self):
        """Fit log X = a + b * log Y on the recent window. b = hedge ratio (in % terms)."""
        xs = [h[0] for h in self.hist]
        ys = [h[1] for h in self.hist]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        vy = sum((y - my) ** 2 for y in ys)
        if vy == 0:
            return None, None
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vy
        return b, mx - b * my

    def step(self):
        c, e = self.c, self.e
        X, Y = best(e.get_last_price_book(c.quote)), best(e.get_last_price_book(c.hedge))
        if not X or not Y:
            return
        x_bid, x_bidv, x_ask, x_askv = X
        y_bid, y_bidv, y_ask, y_askv = Y
        xm, ym = (x_bid + x_ask) / 2, (y_bid + y_ask) / 2
        self.hist.append((math.log(xm), math.log(ym)))
        if len(self.hist) < c.warmup:
            return

        b, a = self._learn()
        if b is None:
            return
        self.ratio = max(0.0, min(b, 3.0))  # guard against silly estimates
        implied = math.exp(a + b * math.log(ym))
        self.fair = fair = xm + c.model_weight * (implied - xm)

        pos = e.get_positions()
        px, py = pos.get(c.quote, 0), pos.get(c.hedge, 0)

        # 1) take mispricing on X outright (book far from fair)
        if x_bid - fair >= c.take_edge and px > -c.max_pos:
            e.insert_order(c.quote, price=x_bid, volume=min(x_bidv, c.quote_size, c.max_pos + px),
                           side="ask", order_type="ioc")
        elif fair - x_ask >= c.take_edge and px < c.max_pos:
            e.insert_order(c.quote, price=x_ask, volume=min(x_askv, c.quote_size, c.max_pos - px),
                           side="bid", order_type="ioc")

        # 2) hedge: hold -ratio * (X value / Y price) lots of Y against our X position
        if c.hedge_on:
            px = e.get_positions().get(c.quote, 0)
            target = -round(self.ratio * px * xm / ym)
            diff = target - py
            if abs(diff) > c.hedge_threshold:
                trade = diff - (c.hedge_threshold // 2) * (1 if diff > 0 else -1)
                if trade > 0:
                    v = min(trade, y_askv, c.max_pos - py)
                    if v > 0:
                        e.insert_order(c.hedge, price=y_ask, volume=v, side="bid", order_type="ioc")
                elif trade < 0:
                    v = min(-trade, y_bidv, c.max_pos + py)
                    if v > 0:
                        e.insert_order(c.hedge, price=y_bid, volume=v, side="ask", order_type="ioc")

        # 3) requote X around fair, skewed by position
        e.delete_orders(c.quote)
        px = e.get_positions().get(c.quote, 0)
        center = fair - c.skew * px
        # cap X so the matching hedge in Y still fits under Y's limit
        cap = c.max_pos if not c.hedge_on else int(min(c.max_pos, c.max_pos / max(self.ratio * xm / ym, 1e-9)))
        bid_v, ask_v = min(c.quote_size, cap - px), min(c.quote_size, cap + px)
        if bid_v > 0:
            e.insert_order(c.quote, price=rnd(center - c.edge, c.tick), volume=bid_v, side="bid", order_type="limit")
        if ask_v > 0:
            e.insert_order(c.quote, price=rnd(center + c.edge, c.tick), volume=ask_v, side="ask", order_type="limit")

    def flatten(self):
        for inst in (self.c.quote, self.c.hedge):
            self.e.delete_orders(inst)
            p = self.e.get_positions().get(inst, 0)
            b = best(self.e.get_last_price_book(inst))
            if p and b:
                side, px = ("ask", b[0]) if p > 0 else ("bid", b[2])
                self.e.insert_order(inst, price=px, volume=abs(p), side=side, order_type="ioc")


def run(e, sleep, interval=0.2, steps=None, cfg: Config = Config(), verbose=True):
    bot = PairBot(e, cfg)
    i = 0
    try:
        while steps is None or i < steps:
            bot.step()
            sleep(interval)
            i += 1
            if verbose and i % 500 == 0:
                r = f"{bot.ratio:.2f}" if bot.ratio is not None else "-"
                print(f"step {i}: pnl {e.get_pnl():8.1f}  ratio {r}  pos {e.get_positions()}")
    finally:
        bot.flatten()
    return bot


if __name__ == "__main__":
    import mock_multi as M
    e = M.Exchange(seed=1)
    e.connect()
    run(e, sleep=e.advance, steps=3000)
    print(f"final pnl {e.get_pnl():.1f}, traded {e.volume_traded} lots")
