"""Compare options_bot settings on 5 mock markets (true vol = 30%): avg PnL, worst run, worst |delta|."""
import mock_options as M
import options_bot as O


def evaluate(seeds=range(5), steps=3000, **kw):
    pnls, worst = [], 0
    for s in seeds:
        e = M.Exchange(seed=s); e.connect()
        b = O.OptionsBot(e, O.Config(**kw), now=e.current_time)  # fresh Config each run
        for _ in range(steps):
            b.step(); e.advance()
            pos = e.get_positions()
            d = pos.get("NVDA", 0) + sum(pos.get(i, 0) * g[1] for i, g in b.greeks.items())
            worst = max(worst, abs(d))
        b.flatten()
        pnls.append(e.get_pnl())
    return sum(pnls) / len(pnls), min(pnls), worst


if __name__ == "__main__":
    grid = [dict(), dict(hedge_on=False), dict(delta_threshold=1),
            dict(vol=0.20, auto_vol=False), dict(vol=0.20), dict(vol=0.40, auto_vol=False), dict(vol=0.40),
            dict(edge=0.15), dict(edge=0.25)]
    print(f"{'settings':<30}{'avg PnL':>9}{'worst':>8}{'max |delta|':>13}")
    for kw in grid:
        a, w, d = evaluate(**kw)
        print(f"{str(kw or 'defaults'):<30}{a:>9.0f}{w:>8.0f}{d:>13.0f}")
