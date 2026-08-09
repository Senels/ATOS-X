# AI Gate — Backtest Contract

ATOS-X backtest karar zincirinde model sinyali doğrudan işlem değildir.

`signal -> confidence gate -> approved/rejected -> cost-aware trade ledger`

## Varsayılanlar

- BUY / SELL: actionable
- HOLD: reject
- minimum confidence: 0.60
- cost model: fee + slippage + funding
- canlı emir gönderimi: yok

## Kayıt

Her karar `timestamp`, `symbol`, `signal`, `approved`, `confidence`, `reason`
ve `model_type` alanlarını taşıyabilir. Onaylanan işlem için gross ve net
return ayrıca kaydedilebilir.

Bu katman Binance Global USDⓈ-M Futures backtest/paper kullanımına yöneliktir.
Canlı işlem güvenliği veya kârlılık garantisi sağlamaz.
