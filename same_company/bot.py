"""Dual-listing bot: make markets on the illiquid listing (B), hedge every fill on the liquid one (A),
and take free arbitrage when B crosses A. Exchange-agnostic: give it any optibook-style Exchange.

Day-of usage (verify the import in their tutorial):
    from optibook.synchronous_client import Exchange
    import time
    e = Exchange(); e.connect()
    run(e, sleep=time.sleep, interval=0.2)
"""
from dataclasses import dataclass


@dataclass
class Config:
    liquid: str = "PHILIPS_A"
    illiquid: str = "PHILIPS_B"
    edge: float = 0.3          # how far from fair we quote on B (our profit per lot, before hedging cost)
    skew: float = 0.005        # shift B quotes by skew per lot of B position, to steer it back to 0
    hedge_threshold: int = 30  # only hedge on A once |A+B| exceeds this (hedging every lot eats the edge)
    quote_size: int = 10
    max_pos: int = 80          # stay below the exchange limit (100) as a safety buffer
    arb_edge: float = 0.05     # min profit per lot to take a cross-listing arbitrage
    hedge: bool = True
    tick: float = 0.1


def best(book):
    if not book.bids or not book.asks:
        return None
    return book.bids[0].price, book.bids[0].volume, book.asks[0].price, book.asks[0].volume


def rnd(x, tick):
    return round(round(x / tick) * tick, 1)


class DualListingBot:
    def __init__(self, e, cfg: Config = Config()):
        self.e, self.c = e, cfg
        self.log = []

    def step(self):
        c, e = self.c, self.e
        A, B = best(e.get_last_price_book(c.liquid)), best(e.get_last_price_book(c.illiquid))
        if not A or not B:
            return
        a_bid, a_bidv, a_ask, a_askv = A
        b_bid, b_bidv, b_ask, b_askv = B
        fair = (a_bid + a_ask) / 2
        pos = e.get_positions()
        pa, pb = pos.get(c.liquid, 0), pos.get(c.illiquid, 0)

        # 1) free arbitrage: B bid above A ask -> sell B, buy A (and the reverse)
        if b_bid - a_ask >= c.arb_edge:
            v = min(b_bidv, a_askv, c.max_pos + pb, c.max_pos - pa, c.quote_size)
            if v > 0:
                e.insert_order(c.illiquid, price=b_bid, volume=v, side="ask", order_type="ioc")
                e.insert_order(c.liquid, price=a_ask, volume=v, side="bid", order_type="ioc")
        elif a_bid - b_ask >= c.arb_edge:
            v = min(b_askv, a_bidv, c.max_pos - pb, c.max_pos + pa, c.quote_size)
            if v > 0:
                e.insert_order(c.illiquid, price=b_ask, volume=v, side="bid", order_type="ioc")
                e.insert_order(c.liquid, price=a_bid, volume=v, side="ask", order_type="ioc")

        # 2) hedge: keep total exposure (A + B) near zero using the liquid listing
        if c.hedge:
            pos = e.get_positions()
            pa, pb = pos.get(c.liquid, 0), pos.get(c.illiquid, 0)
            net = pa + pb
            excess = net - c.hedge_threshold // 2 if net > c.hedge_threshold else \
                     net + c.hedge_threshold // 2 if net < -c.hedge_threshold else 0
            net = excess  # hedge back to half the threshold, not all the way to 0
            if net > 0:
                v = min(net, a_bidv, c.max_pos + pa)
                if v > 0:
                    e.insert_order(c.liquid, price=a_bid, volume=v, side="ask", order_type="ioc")
            elif net < 0:
                v = min(-net, a_askv, c.max_pos - pa)
                if v > 0:
                    e.insert_order(c.liquid, price=a_ask, volume=v, side="bid", order_type="ioc")

        # 3) requote B around fair, skewed by our B position (long -> quote lower to sell it off)
        e.delete_orders(c.illiquid)
        pb = e.get_positions().get(c.illiquid, 0)
        center = fair - c.skew * pb
        bid_px, ask_px = rnd(center - c.edge, c.tick), rnd(center + c.edge, c.tick)
        bid_v = min(c.quote_size, c.max_pos - pb)
        ask_v = min(c.quote_size, c.max_pos + pb)
        if bid_v > 0:
            e.insert_order(c.illiquid, price=bid_px, volume=bid_v, side="bid", order_type="limit")
        if ask_v > 0:
            e.insert_order(c.illiquid, price=ask_px, volume=ask_v, side="ask", order_type="limit")

    def flatten(self):
        """End of session / emergency: pull quotes and close everything at the touch."""
        for inst in (self.c.liquid, self.c.illiquid):
            self.e.delete_orders(inst)
            p = self.e.get_positions().get(inst, 0)
            b = best(self.e.get_last_price_book(inst))
            if p and b:
                side, px = ("ask", b[0]) if p > 0 else ("bid", b[2])
                self.e.insert_order(inst, price=px, volume=abs(p), side=side, order_type="ioc")


def run(e, sleep, interval=0.2, steps=None, cfg: Config = Config(), verbose=True):
    bot = DualListingBot(e, cfg)
    i = 0
    try:
        while steps is None or i < steps:
            bot.step()
            sleep(interval)
            i += 1
            if verbose and i % 500 == 0:
                print(f"step {i}: pnl {e.get_pnl():8.1f}  pos {e.get_positions()}")
    finally:
        bot.flatten()
    return bot


if __name__ == "__main__":
    from mock_optibook import Exchange
    e = Exchange(seed=1)
    e.connect()
    run(e, sleep=e.advance, steps=3000)
    print(f"final pnl {e.get_pnl():.1f}, traded {e.volume_traded} lots, pos {e.get_positions()}")
