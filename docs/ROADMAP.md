# ATOS X — Mimari Dokümanlar

## Sprint Planı

| # | Sprint | İçerik | Durum |
|---|---|---|---|
| 1 | Core Foundation | config, FastAPI iskeleti, sqlite DB | ✅ |
| 2 | Backtest | gerçek OHLCV motoru, toplu tarama, optimizasyon | ✅ |
| 3 | Binance Connector | async REST (signed), websocket akışları | ✅ |
| 4 | Market Collector | kline/mark price toplama, backfill | ✅ |
| 5 | Dashboard | canlı PnL/pozisyon, risk kartı, koruma rozetleri | ✅ |
| 6 | Market Intelligence | rejim tespiti, volatilite, likidite analizi | ✅ |
| 7 | Coin Intelligence | sembol seçimi, momentum/score motoru | ✅ |
| 8 | Decision Council | çoklu sinyal oylaması, açıklanabilir karar | ✅ |
| 9 | Governor | risk limitleri, konsantrasyon engelleri, koruma tamiri | ✅ |
| 10 | Trading | emir yönetimi, TP/SL/trailing, portföy senkronu | ✅ |

> Not: Tüm sprintler tamamlandı. Operasyon katmanı eklendi: Telegram
> komutları (`/sl /tp /koruma /kapat /kapatall /sinyal /rapor /risk /gecmis`),
> risk durumu restart kalıcılığı, canlı giriş kapısı (Decision Council),
> market rejim/score/dashboard kartları, kline collect/backfill arşivi,
> borsa bakiye senkronu ve `/api/v1/portfolio` özeti.
> Tüm detaylar `docs/OPS.md`'de.

## Veri

Backtest için `legacy/data/futures_4h_data/` (ve 30m/15m/2h) yerel
OHLCV CSV arşivi kullanılır; geri kalan eski legacy kodları
(eski XAU bot, araştırma scriptleri, TradingView stratejileri)
arşivden çıkarılmıştır (git geçmişinde mevcuttur).
