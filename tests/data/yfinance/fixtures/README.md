# YFinanceCache fixtures

Static test fixtures for the Yahoo Finance backtest adapter
(`trading_system.data.yfinance`). The on-disk format here is
identical to what `YFinanceCache.put_bars` / `put_dividends` write
in production — no codec gymnastics, just JSON Lines with `Decimal`
as TEXT and `datetime` as ISO-8601.

## Layout

```
fixtures/
└── ASML.AS/
    ├── 1d/
    │   └── 2026-01-01T00:00:00+00:00__2026-01-08T00:00:00+00:00_bars.jsonl
    └── dividends/
        └── 2026.jsonl
```

The directory hierarchy mirrors `YFinanceCache`'s layout:
- `<symbol>/<timeframe>/<start>__<end>_bars.jsonl` for OHLCV.
- `<symbol>/dividends/<year>.jsonl` for dividend events.

## Why static files

The fixtures are committed (rather than generated in tests) for
three reasons:

1. **Format anchor.** They demonstrate exactly what the recorder
   script will emit; if the on-disk format ever drifts, these
   fixtures will fail to load and the deviation is caught.
2. **Replay determinism (REQ_NF_DAT_001).** The integration test
   reads these files; subsequent runs are bit-identical against
   the same fixture content.
3. **Hermetic CI.** No network call is needed — the fixture is the
   data.

## Refreshing

For now the bars are synthetic (deterministic test data; documented
in `tests/data/yfinance/test_integration.py`'s docstring). Once the
recorder script lands, real EU-stock bars will be sourced via
`tools/yfinance_recorder.py --symbol ASML.AS --start ... --end ...`
and committed alongside this README.

## Adding a new fixture

1. Run the recorder once with `--allow-network`.
2. Inspect the resulting JSON Lines file under the cache root.
3. Copy the file into this fixtures tree at the matching path.
4. Reference it from a test via `_load_fixture_cache(tmp_path)`
   (see `test_integration.py`).
