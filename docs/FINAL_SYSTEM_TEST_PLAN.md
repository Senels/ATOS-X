# ATOS-X Final System Test Plan

This is the release gate before live trading is considered.

## Static and imports
- Python compileall
- package import checks
- configuration/schema validation

## Unit
- AI features and labels
- leakage-safe training
- AI gate
- backtest cost model
- dashboard view model
- risk rules

## Integration
- Binance market-data adapter in mocked/test mode
- websocket reconnect/state recovery
- database persistence/restart recovery
- strategy -> AI -> risk -> execution pipeline
- position reconciliation

## Backtest regression
- deterministic fixtures
- fee, slippage and funding
- TP/SL
- long/short accounting
- equity curve and drawdown
- Sharpe, Sortino and Profit Factor
- approved vs rejected signal attribution

## AI safety
- no random temporal split
- train-only scaler
- purge/embargo
- stale-data rejection
- invalid confidence rejection
- model failure -> HOLD

## Operational
- kill switch
- daily loss halt
- max drawdown halt
- exposure limits
- restart with open positions
- duplicate event/order protection

## Live readiness
- Binance Global USDⓈ-M Futures only
- secrets/env for credentials
- PAPER default
- LIVE requires explicit operator confirmation

## Result contract
A system test is PASS only when the GitHub Actions Quality Gate is green and there are no unresolved critical failures. If a test cannot execute, it must be recorded as NOT RUN, never PASS.
