# Team guide: hedging and options bots for Optibook

This kit complements the market-making bot (`optibook_mm_bot.py`). That bot quotes all seven AI stocks independently. These bots add two things it doesn't have: **hedging across related stocks** and **options trading**. Every bot here has a practice exchange (mock) and a tuning script, so you can test changes before touching the real exchange.

## Which bot to run

| Situation on the day | Run | One-line idea |
|---|---|---|
| Plain stocks only | Teammate's `optibook_mm_bot.py` | Quote all 7 stocks, skew to stay flat |
| Stocks clearly move together (pairs/cointegration hint) | `pair_finder.py`, then `pair_bot.py` | Quote the quiet stock, hedge it with a related busy one |
| Same company listed twice (e.g. `X_A` / `X_B`) | `bot.py` | Quote the quiet listing, hedge 1:1 on the busy one |
| Options appear (calls/puts, VEGA on leaderboard) | `options_bot.py` | Price with Black-Scholes, quote around it, delta-hedge with the stock |

All bots share the same loop: **price it, quote it, hedge it, flatten on exit (Ctrl+C).**

## Files

| File | Purpose |
|---|---|
| `pair_finder.py` | Watches every stock and ranks which pairs move together |
| `pair_bot.py` + `mock_multi.py` + `tune_pair.py` | Pair bot, practice exchange, settings comparison |
| `options_bot.py` + `mock_options.py` + `tune_options.py` | Options bot, practice exchange, settings comparison |
| `bot.py` + `mock_optibook.py` + `tune.py` | Dual-listing bot, practice exchange, settings comparison |

```bash
pip install numpy pandas
python pair_finder.py     # each script runs on its mock by default
python pair_bot.py
python options_bot.py
python tune_pair.py       # compare settings on 5 mock markets
```

## 1. Pair finder

Run this first on the real exchange. It samples every stock's mid for a few minutes and prints, for each pair:

- **corr**: how closely their price changes move together. Near 1 = good hedge.
- **lvl_b**: the hedge ratio. 1.3 means X moves 1.3% for every 1% of Y.
- **half_life**: how fast the gap between them closes. Small = tied together (cointegrated).

Pick X = a wide-spread stock (CRWV, NBIS) and Y = the stock with high corr and small half_life against it. On the mock it found CRWV–NVDA with ratio 1.32 (true value 1.3).

## 2. Pair bot (`pair_bot.py`)

Every loop:
1. **Learn** the ratio between X and Y from the last 300 prices.
2. **Theo of X**: halfway between X's own mid and what Y's price implies X should be.
3. **Take** X immediately if its book is far through theo.
4. **Hedge**: hold about `-ratio × X position × (X price / Y price)` shares of Y. Only trade Y when the hedge is off by more than `hedge_threshold`.
5. **Quote** X at theo ± `edge`, skewed by position. X size is capped so the hedge still fits under Y's position limit.

| Setting | Default | Meaning |
|---|---|---|
| `quote`, `hedge` | CRWV, NVDA | X and Y |
| `model_weight` | 0.5 | 0 = theo is X's own mid, 1 = fully Y-implied |
| `edge` | 0.4 | Distance of quotes from theo |
| `take_edge` | 0.4 | How far off a book must be before taking it |
| `hedge_threshold` | 15 | Hedge error tolerated before trading Y |
| `skew` | 0.005 | Quote shift per share held |
| `max_pos` | 80 | Safety buffer under the exchange limit |

Mock results (avg of 5 runs): defaults ~1,360; hedging every share ~780 (paying the spread twice eats the edge); theo from X's own mid only ~770; no hedge ~2,100 but twice the risk. **Lesson: using the related stock for theo helps most, and hedge in batches, not per share.**

## 3. Options bot (`options_bot.py`)

Every loop:
1. **Learn volatility** from the market: implied vol of near-the-money options, smoothed.
2. **Price** every option with Black-Scholes: fair value, delta, vega.
3. **Take** options whose book is far through fair.
4. **Quote** each option at fair ± `edge`, skewed by position. Stop adding to the side that grows total vega past `max_vega`.
5. **Delta hedge**: if total delta (stock + options × delta) passes `delta_threshold`, trade the stock back.

| Setting | Default | Meaning |
|---|---|---|
| `stock` | NVDA | Underlying |
| `vol` / `auto_vol` | 0.30 / on | Starting vol; learn it from the market |
| `edge` | 0.2 | Distance of quotes from fair |
| `delta_threshold` | 8 | Delta tolerated before hedging |
| `max_vega` | 300 | Total vega cap |
| `max_option_pos` | 50 | Per option |

Mock results: defaults ~3,000; **wrong vol guess without learning: −760**; wrong guess with learning ~2,800. **Lesson: volatility is the key input, so always let it learn from the market.**

Option fields the bot reads: `base_instrument_id`, `strike`, `expiry`, `option_kind`. Check the real names in Optiver's example code.

## 4. Dual-listing bot (`bot.py`)

Same as the pair bot but for one company on two listings: hedge ratio is 1:1, plus free arbitrage when one listing's bid is above the other's offer. Defaults: `edge` 0.3, `hedge_threshold` 30. Only relevant if instruments like `X_A` / `X_B` appear.

## Day-of checklist
1. Get Optiver's example bot running first.
2. Compare their function names with the mocks (`get_last_price_book`, `insert_order`, `delete_orders`, `get_positions`, `get_instruments`) and fix differences.
3. Set `max_pos` below their position limit.
4. Run on the real exchange:
```python
from optibook.synchronous_client import Exchange   # use the import from their example
import time, pair_bot
e = Exchange(); e.connect()
pair_bot.run(e, sleep=time.sleep, interval=0.2, cfg=pair_bot.Config(quote="CRWV", hedge="NVDA"))
```
5. Change one setting at a time and compare PnL, Position and Delta on the leaderboard.

## Known limitations (to fix next)
- **Theo includes our own quotes.** The bots take the mid of the whole book, so once our quotes are the best price, theo chases itself. Fix: exclude our orders (`get_outstanding_orders`) before computing the mid, as the market-making bot does.
- **Cancel-and-replace every loop** loses queue priority and uses up the message limit. Fix: only requote when the target price changes.
- **No pull on price jumps** yet. Borrow the market-making bot's `JUMP_TICKS` / `COOLDOWN_SECONDS` logic.
- **Mock numbers are illustrative.** The mocks are invented markets; re-tune on the real exchange.
