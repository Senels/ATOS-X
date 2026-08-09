# AI → Backtest Pipeline

ATOS-X artık mevcut bar-tabanlı backtest motorunun önüne açık bir AI karar kapısı koyabilir:

`strategy signal → AI confidence gate → APPROVED/REJECTED → BacktestEngine → P/L + risk metrics`

## Tasarım

- `backend/app/backtest/ai_gate.py`: karar sözleşmesi ve maliyet modeli.
- `backend/app/backtest/ai_pipeline.py`: AI gate'i mevcut `BacktestEngine` ile bağlayan adapter.
- `backend/app/backtest/engine.py`: fill, SL/TP, position sizing, equity curve ve risk metriklerinin yürütülmesi.

## Önemli sınır

AI adapter strateji sinyalini değiştirmez; yalnızca girişin AI tarafından onaylanıp onaylanmadığını engine'e iletir. Bu nedenle mevcut backtest mekanikleri korunur.

Varsayılan confidence eşiği `0.60`'tır. BUY/SELL actionable, HOLD reddedilir.

Funding oranı karar ledger'ında taşınır. Mevcut engine'in funding'ı bar bazında gerçek Binance funding schedule'ına bağlaması ayrı bir entegrasyon adımıdır; burada funding değeri otomatik olarak uydurulmaz.
