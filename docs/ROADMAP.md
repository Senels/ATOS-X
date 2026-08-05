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
| 11 | TTPTSL Motor | optimize_ttp.py durum makinesi → TtpTsl (analyze_full/manage) + engine managed yol + canlı pozisyon yönetimi | ✅ |

> Not: Sprint 11, `optimize_ttp.py run_backtest`'in tam state machine'ini
> (sl_trail_mode, tp_qty_pct kısmi TP, breakeven, reversal, trailing TP)
> `TtpTsl`'e taşıdı: `analyze_full` (backtest/optimizasyon sözleşmesi) ve
> `manage` (canlı pozisyon yönetimi, `tp_already_hit` ile kısmi TP tekrarını
> önler). `BacktestEngine.run` managed modda per-bar SL/TP + çıkış direktiflerini
> uygular (`_partial_close` dahil); v23 yolu değişmez. `AutoTrader` ttp modunda
> pozisyonları `manage` ile yönetir (kısmi kapanış, trailing/breakeven SL, SL/TP
> tazeleme). Detaylar `.opencode/plans/ttp_live_management_plan.md`'de.

> Not: Tüm sprintler tamamlandı. Operasyon katmanı eklendi: Telegram
> komutları (`/sl /tp /koruma /kapat /kapatall /sinyal /rapor /risk /gecmis
> /istatistik /veri /backfill /giris /yedek /yedekler /geriyukle`), risk
> durumu restart kalıcılığı, canlı giriş kapısı (Decision Council), market
> rejim/score/dashboard kartları, kline collect/backfill arşivi + otomatik
> backfill, borsa bakiye senkronu ve `/api/v1/portfolio` özeti. Güvenlik
> katmanı: Telegram chat whitelist, API-key koruması, CORS. Canlı trading:
> paper/kill-switch/testnet/live modları, `halt_entries` + `/giris`, emir
> gönderimi açık onayla. DB yedekleme/geri yükleme (integrity check, 6 saatlik
> periyodik loop, Telegram hata bildirimi). Tüm detaylar `docs/OPS.md`'de.

## Veri

Backtest için `legacy/data/futures_4h_data/` (ve 30m/15m/2h) yerel
OHLCV CSV arşivi kullanılır; geri kalan eski legacy kodları
(eski XAU bot, araştırma scriptleri, TradingView stratejileri)
arşivden çıkarılmıştır (git geçmişinde mevcuttur).
