"""Try bot settings on several mock market seeds; prints avg PnL, worst run, and worst exposure."""
import mock_optibook as M
import bot


def evaluate(seeds=range(5), steps=3000, **kw):
    pnls, worst_net = [], 0
    for s in seeds:
        e = M.Exchange(seed=s); e.connect()
        b = bot.DualListingBot(e, bot.Config(**kw))
        for _ in range(steps):
            b.step(); e.advance()
            worst_net = max(worst_net, abs(sum(e.get_positions().values())))
        b.flatten()
        pnls.append(e.get_pnl())
    return sum(pnls) / len(pnls), min(pnls), worst_net


if __name__ == "__main__":
    grid = [dict(hedge=False, skew=0), dict(hedge_threshold=0), dict()] + \
           [dict(edge=ed, hedge_threshold=ht) for ed in (0.2, 0.3) for ht in (20, 40)]
    print(f"{'settings':<45}{'avg PnL':>9}{'worst':>9}{'max |A+B|':>11}")
    for kw in grid:
        avg, worst, net = evaluate(**kw)
        print(f"{str(kw or 'defaults'):<45}{avg:>9.0f}{worst:>9.0f}{net:>11}")
