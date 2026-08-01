# ATOS X — Mimari Dokümanlar

## Sprint Planı

| # | Sprint | İçerik | Durum |
|---|---|---|---|
| 1 | Core Foundation | config, event bus, FastAPI iskeleti, DB bağlantısı, Docker | ✅ |
| 2 | Infrastructure | TimescaleDB şemaları, Redis, alembic migration, CI | ✅ |
| 3 | Binance Connector | async REST (signed), websocket akışları, rate limiter | ⏳ |
| 4 | Market Collector | kline/mark price toplama, cache, backfill | ⏳ |
| 5 | Dashboard | React + TS + Tailwind, canlı PnL/pozisyon | ⏳ |
| 6 | Market Intelligence | rejim tespiti, volatilite, likidite analizi | ⏳ |
| 7 | Coin Intelligence | sembol seçimi, momentum/score motoru | ⏳ |
| 8 | Decision Council | çoklu sinyal oylaması, açıklanabilir karar | ⏳ |
| 9 | Governor | risk limitleri, kill-switch, pozisyon boyutlandırma | ⏳ |
| 10 | Trading | emir yönetimi, TP/SL/trailing, portföy senkronu | ⏳ |

## Legacy (Eski Sistem)

`legacy/bot/` eski momentum botudur ve yeni mimariye geçiş öncesi
referans olarak korunur. Kritik stop-order bug'ı düzeltilmiştir
(commit `9a09675`): girişte exchange-side SL/TP, çıkışta gerçek
market close, restart sonrası pozisyon senkronu.
