# ATOS X — Mimari Dokümanlar

## Sprint Planı

| # | Sprint | İçerik | Durum |
|---|---|---|---|
| # | Sprint | İçerik | Durum |
|---|---|---|---|
| 1 | Core Foundation | config, FastAPI iskeleti, sqlite DB | ✅ |
| 2 | Backtest | gerçek OHLCV motoru, toplu tarama, optimizasyon | ✅ |
| 3 | Binance Connector | async REST (signed), websocket akışları | ✅ |
| 4 | Market Collector | kline/mark price toplama, backfill | ⏳ |
| 5 | Dashboard | canlı PnL/pozisyon | ⏳ |
| 6 | Market Intelligence | rejim tespiti, volatilite, likidite analizi | ⏳ |
| 7 | Coin Intelligence | sembol seçimi, momentum/score motoru | ⏳ |
| 8 | Decision Council | çoklu sinyal oylaması, açıklanabilir karar | ⏳ |
| 9 | Governor | risk limitleri, kill-switch, pozisyon boyutlandırma | ⏳ |
| 10 | Trading | emir yönetimi, TP/SL/trailing, portföy senkronu | ⏳ |

## Veri

Backtest için `legacy/data/futures_4h_data/` (ve 30m/15m/2h) yerel
OHLCV CSV arşivi kullanılır; geri kalan eski legacy kodları
(eski XAU bot, araştırma scriptleri, TradingView stratejileri)
arşivden çıkarılmıştır (git geçmişinde mevcuttur).
