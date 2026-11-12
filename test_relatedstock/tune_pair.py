"""Compare pair_bot settings on 5 mock markets: avg PnL, worst run, worst hedged exposure (in Y lots)."""
import mock_multi as M
import pair_bot as P


def evaluate(seeds=range(5), steps=3000, **kw):
    pnls, worst = [], 0
    for s in seeds:
        e = M.Exchange(seed=s); e.connect()
        b = P.PairBot(e, P.Config(**kw))
        for _ in range(steps):
            b.step(); e.advance()
            pos = e.get_positions()
            risk = pos["CRWV"] * 1.3 * e.fair_price("CRWV") / e.fair_price("NVDA") + pos["NVDA"]
            worst = max(worst, abs(risk))
        b.flatten()
        pnls.append(e.get_pnl())
    return sum(pnls) / len(pnls), min(pnls), worst


if __name__ == "__main__":
    grid = [dict(hedge_on=False), dict(hedge_threshold=0), dict(), dict(model_weight=0),
            dict(model_weight=1), dict(edge=0.3), dict(edge=0.5)]
    print(f"{'settings':<35}{'avg PnL':>9}{'worst':>8}{'max risk':>10}")
    for kw in grid:
        a, w, r = evaluate(**kw)
        print(f"{str(kw or 'defaults'):<35}{a:>9.0f}{w:>8.0f}{r:>10.0f}")
