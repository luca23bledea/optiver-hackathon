"""Watch all instruments for a while, then report which pairs move together.
Run this FIRST on the real exchange to pick the quote/hedge pair for pair_bot.py.

    corr       : correlation of price changes (close to 1 = they move together; good hedge)
    beta       : how much X moves (in %) when Y moves 1%  -> the hedge ratio
    lvl_b      : same ratio measured on price levels (more reliable when half_life is small)
    half_life  : how fast the gap between them closes, in samples (small = tied together/cointegrated)
"""
import math
import itertools
import numpy as np


def sample_mids(e, sleep, n=600, interval=0.2):
    insts = e.get_instruments()
    insts = list(insts) if not isinstance(insts, dict) else list(insts.keys())
    data = {i: [] for i in insts}
    for _ in range(n):
        for i in insts:
            b = e.get_last_price_book(i)
            data[i].append((b.bids[0].price + b.asks[0].price) / 2 if b.bids and b.asks else np.nan)
        sleep(interval)
    return {i: np.array(v) for i, v in data.items()}


def analyse(mids):
    rows = []
    for x, y in itertools.permutations(mids, 2):
        lx, ly = np.log(mids[x]), np.log(mids[y])
        ok = ~(np.isnan(lx) | np.isnan(ly))
        lx, ly = lx[ok], ly[ok]
        if len(lx) < 50:
            continue
        dx, dy = np.diff(lx), np.diff(ly)
        corr = np.corrcoef(dx, dy)[0, 1]
        beta = np.cov(dx, dy)[0, 1] / np.var(dy)
        b, a = np.polyfit(ly, lx, 1)                  # level relationship log X = a + b log Y
        resid = lx - (a + b * ly)
        rho = np.polyfit(resid[:-1], resid[1:], 1)[0]  # AR(1) of the gap
        hl = -math.log(2) / math.log(rho) if 0 < rho < 1 else float("inf")
        rows.append((x, y, corr, beta, b, hl))
    rows.sort(key=lambda r: -r[2])
    print(f"{'quote X':<8}{'hedge Y':<8}{'corr':>7}{'beta':>7}{'lvl_b':>7}{'half_life':>11}")
    for x, y, c, bt, lb, hl in rows[:10]:
        print(f"{x:<8}{y:<8}{c:>7.2f}{bt:>7.2f}{lb:>7.2f}{hl:>11.1f}")
    return rows


if __name__ == "__main__":
    import mock_multi as M
    e = M.Exchange(seed=3)
    e.connect()
    analyse(sample_mids(e, sleep=e.advance, n=1500))
