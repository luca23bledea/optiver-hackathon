# optibook_kit — bots for the Optiver challenge

## Which bot when?

| If the challenge has... | Use | Idea |
|---|---|---|
| The same company on two listings (e.g. `X_A` / `X_B`) | `bot.py` | Quote the quiet listing, hedge 1:1 on the busy one, grab free arbitrage when prices cross |
| Different but related stocks (NVDA, CRWV, ...) | `pair_bot.py` | Quote the quiet stock, hedge on a related busy stock using a ratio learned from live prices |
| Options on a stock (calls/puts, VEGA on leaderboard) | `options_bot.py` | Price options with Black-Scholes, quote around fair, delta-hedge with the stock |
| Something else (ETF, news) | None as-is | Reuse the same loop: price it -> quote it -> hedge it -> flatten |

## Files

| File | What it is |
|---|---|
| `bot.py` | Dual-listing bot (unchanged) |
| `mock_optibook.py`, `tune.py` | Practice exchange + settings comparison for `bot.py` |
| `pair_bot.py` | Pair bot for two related stocks |
| `pair_finder.py` | Watches all stocks and ranks which pairs move together. Run this first |
| `mock_multi.py`, `tune_pair.py` | Practice exchange with NVDA/MU/AMD/NBIS/CRWV + settings comparison for `pair_bot.py` |
| `options_bot.py` | Options market maker with delta hedge and implied-vol learning |
| `mock_options.py`, `tune_options.py` | Practice exchange with NVDA + 10 options, settings comparison |

```bash
python bot.py          # dual-listing bot on mock
python pair_finder.py  # find related pairs on mock
python pair_bot.py     # pair bot on mock
python tune_pair.py    # compare pair bot settings
```

## How pair_bot thinks, every loop
1. **Learn:** fit "log X = a + ratio * log Y" on the last 300 prices. `ratio` = how many % X moves per 1% of Y.
2. **Fair price of X:** halfway between X's own mid and what Y's price implies X should be (`model_weight`).
3. **Take:** if X's book is more than `take_edge` away from fair, trade it immediately.
4. **Hedge:** hold about `-ratio * X position * (X price / Y price)` lots of Y. Only trade Y when off by more than `hedge_threshold`.
5. **Quote:** cancel and repost X buy/sell orders `edge` around fair, shifted by position (`skew`).
6. **Exit:** Ctrl+C cancels everything and closes all positions.

## How options_bot thinks, every loop
1. **Learn vol:** compute implied volatility from near-the-money option prices, smooth it into `vol`.
2. **Price:** Black-Scholes fair value, delta and vega for every option from the stock's mid.
3. **Take:** buy/sell any option whose book is more than `take_edge` through fair.
4. **Quote:** buy/sell orders `edge` around fair, skewed by position; stop adding vega beyond `max_vega`.
5. **Delta hedge:** if total delta (stock + options x delta) exceeds `delta_threshold`, trade the stock back.
6. **Exit:** Ctrl+C flattens everything.

Option fields used: `base_instrument_id`, `strike`, `expiry`, `option_kind`. Check their real names on the day.

## Day-of steps
1. Get their example bot running in their VSCode first.
2. Check method names against the mocks (`get_last_price_book`, `insert_order`, `delete_orders`, `get_positions`, `get_instruments`) and fix any differences.
3. Run `pair_finder.py` on the real exchange (swap the mock for their `Exchange`, `sleep=time.sleep`). Pick X = wide-spread stock, Y = the one with high `corr` and small `half_life`.
4. Run the bot with those names and `max_pos` below their position limit.
5. Watch PnL for 10 minutes, then tune `edge` and `hedge_threshold`.

```python
from optibook.synchronous_client import Exchange   # use the import from their example
import time, pair_bot
e = Exchange(); e.connect()
pair_bot.run(e, sleep=time.sleep, interval=0.2, cfg=pair_bot.Config(quote="CRWV", hedge="NVDA"))
```
