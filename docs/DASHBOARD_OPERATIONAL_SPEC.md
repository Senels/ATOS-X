# ATOS-X Dashboard — Operational Specification

## Principle
The main dashboard is a decision surface, not a data warehouse. Every element must answer one of four questions: What is happening? Is it safe? What does AI want to do? What needs my attention?

## Layer 0 — System status
- Binance Global USDⓈ-M Futures connection
- WebSocket health / data freshness
- Trading mode: PAPER / LIVE
- Kill switch

## Layer 1 — Portfolio
Primary cards only:
- Equity
- Today P&L
- Drawdown
- Open positions
- Net exposure
- Margin utilization

## Layer 2 — Risk
Show only active constraints and exceptions:
- Risk state
- Daily loss limit
- Max drawdown state
- Exposure concentration
- Margin warning
- Execution lock / halt reason

## Layer 3 — AI decision pipeline
One compact flow:
MARKET → STRATEGY → AI GATE → RISK GATE → EXECUTION

For the selected opportunity show:
- Symbol
- Direction
- Confidence
- Approval/rejection
- Primary rejection reason
- Model version

## Layer 4 — Opportunities
Ranked table, maximum 8 visible rows by default:
- Symbol
- Direction
- Confidence
- Regime
- Risk grade
- Signal age
- Status

No raw indicator dump on the home screen.

## Layer 5 — Positions
Only live decision fields:
- Symbol / side
- Size
- Entry
- Mark
- Unrealized P&L
- SL / TP status
- Distance to liquidation
- Position risk

Detailed order history remains a secondary screen.

## Layer 6 — Exceptions / alerts
Only actionable alerts. Informational noise is suppressed.
Severity order: CRITICAL → HIGH → WARNING.

## Secondary modules
These do not occupy the main dashboard:
- Backtest
- Walk-forward
- Strategy laboratory
- AI training
- Dataset quality
- Optimization
- News / macro research
- Detailed execution logs
- API diagnostics
- System administration

## UX rules
1. No duplicate metric cards.
2. No raw model telemetry on the home screen.
3. No chart without a decision purpose.
4. Exceptions take precedence over normal status.
5. Drill-down is mandatory for detailed data.
6. Mobile layout prioritizes Risk, AI Decision, Positions, then Opportunities.
7. LIVE trading controls require explicit confirmation.
8. Dashboard must remain useful when market data is temporarily stale.

## Navigation
Dashboard | Markets | Positions | AI | Backtest | Strategies | Risk | News | System

The home screen is intentionally compact; depth belongs in the corresponding module.
