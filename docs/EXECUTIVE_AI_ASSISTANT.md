# ATOS-X Executive AI Assistant

## Rol

`EXECUTIVE_AI_ADMIN` — ATOS-X'in yönetim ve istihbarat katmanı.

Asistan; piyasa, strateji, AI modeli, 50 uzman ajan konseyi, risk, execution,
portföy, veri ve sistem durumunu tek bir yönetim görünümünde birleştirir.

## Uzmanlık katmanları

1. **Market Intelligence** — trend, momentum, volatility, liquidity, correlation
2. **Strategy Intelligence** — v23/TTP, MTF, SL/TP, signal strength
3. **AI/ML** — TensorFlow predictor, confidence, labels, analog memory, retraining
4. **Agent Council** — quorum, category agreement, deliberation, risk veto
5. **Risk Management** — DD, daily loss, equity floor, concentration, leverage, VaR/CVaR, stress
6. **Execution** — Binance Global USDⓈ-M Futures, paper/live, fee, slippage, funding
7. **Portfolio Analytics** — PnL, win rate, PF, Sharpe, Sortino, Calmar, MDD
8. **Data & System** — freshness, OHLCV, OI/funding/orderbook, WebSocket, DB, backup

## Yönetici yetkisi

Asistan tüm bu alanları okuyabilir, kararları açıklayabilir ve yönetici eylemleri
planlayabilir. Ancak ikinci ve kontrolsüz bir emir kanalı oluşturmaz.

Piyasa etkili eylemler normal güvenlik zincirini geçmek zorundadır:

`Assistant → Action Plan → Risk/Permission Checks → Existing Execution Path`

Asistan hiçbir zaman aşağıdakileri bypass edemez:

- Risk gate
- AI gate
- Agent risk veto
- Live trading kill-switch
- API authentication
- Audit trail

Pozisyon kapatma, live/paper mode değişimi ve trading start/stop gibi geri
alınamaz veya doğrudan piyasa etkili işlemler açık confirmation gerektirir.

## Dashboard kullanımı

Ana dashboard yalnızca özet karar yüzeyini gösterir. Assistant paneli ise:

- **Executive Brief**
- **Why? / Evidence**
- **Risk Assessment**
- **AI & Council Verdict**
- **Action Plan**
- **Exceptions**
- **Deep Dive**

katmanlarına ayrılır.
