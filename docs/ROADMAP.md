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
| 12 | AI Katmanı | TensorFlow yön tahmini (`app/ai/`), görünürlük (signals API/Telegram/dashboard/koruma editor), doğruluk izleme (`predictions` tablosu, bar-bazlı 12 bar sonrası çözümleme, `/api/v1/ai/stats`, `/ai` komutu) | ✅ |
| 13 | Model Kalitesi + Risk | horizon/özellik deneyleri (h24 + 23 özellik kazandı, `ai_horizon=24` parametrik zincir), volatilite rejimi pozisyon boyutlandırma (`atr_ratio`, A/B doğrulandı) | ✅ |
| 14 | Geçiş Politikası + Kalibrasyon | restore yaş politikası, TTP exit A/B, AI eşik + sembol kalite taraması | 🔄 |

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

> Not: Sprint 12 (AI Katmanı): `backend/app/ai/` TensorFlow modeli
> (`scripts/train_ai.py`, tensorflow-intel==2.15.1 — numpy-1 ABI; pandas
> yüklüyken TF 2.21 DLL verir). Model `backend/app/models/ai_direction/`
> (gitignore'lı) `predictor.load()` ile yüklenir; TF yoksa/yüklenemezse
> predictor None olur ve sistem çalışmaya devam eder (graceful). AI tahmini
> `/api/v1/signals` + Telegram sinyal/pozisyon bildirimlerinde + dashboard
> Live Signals sütununda görünür. Doğruluk izleme: her BUY/SELL sinyali
> `predictions` tablosuna yazılır (bar_ts ile), 12 bar sonraki kapanışla
> hit/miss çözülür; özet `/api/v1/ai/stats` ve `/ai` Telegram komutuyla +
> dashboard `🤖 AI Feedback` kartında (`model_name`, isabet, yön bazlı tablo).
> **Kayıt semantiği (2dc9d57)**: kayıt üç kapıdan (güç/council/AI) ÖNCE
> yapılır — council/güç engeli sinyali feedback döngüsünden kaçırmaz;
> `executed=1` yalnızca üç kapının tümünden geçilirse; (symbol, bar_ts)
> tekillemesi tarama döngüsünün aynı barı mükerrer kaydetmesini önler,
> sonradan geçen sinyalin kaydı executed=1'e yükseltilir. Değerlendirme:
> `scripts/eval_ai.py` (arşivde canlı semantiğiyle hızlı acc). Backtest
> simülasyonu: `scripts/ai_backtest.py --strategy v23|ttp` (motor
> `BacktestEngine.run(..., ai_blocks=)`) — TTP ölçümleri (60/200/550 sembol)
> ve v23 ölçümü docs/OPS.md'de. Otomatik yeniden eğitim: `ai_auto_retrain`
> açıkken 15 dakikada bir tetikleyiciler değerlendirilir (zaman
> `ai_retrain_interval_hours` veya accuracy `ai_retrain_min_acc` +
> `ai_retrain_min_samples` + 6h soğuma); eğitim ayrı süreçte koşar, başarıda
> predictor cache'i temizlenir (restart gerekmez). `scan_limit` ayarlanabilir
> (`/koruma scan_limit <N>`, varsayılan 50; canlıda 200). settings.json BOM
> toleranslı yüklenir (`utf-8-sig`) — dosya **PowerShell ile değil Python ile**
> yazılmalıdır.

> Not: Sprint 13 (Model Kalitesi + Risk): horizon deneyleri (eval_ai 79 sembol) —
> h6 0.591 / h12 0.611 / h24 0.610; son 1 ayda h24 üstün (0.603 vs 0.584). Yeni
> özellikler (`vol_regime`, `ema100_r`, `vol_mom`, `bb_pos`; 19→23) h12'de genel
> 0.622 (+1.1). Kazanan: 23 özellik + h24 (genel 0.625, son 1 ay 0.621) →
> `ai_direction` bu konfigürasyonla yeniden eğitildi (eval 0.623). Zincir
> parametrik: `ai_horizon=24`/`ai_atr_mult` (settings), meta'da horizon,
> feedback çözümleme model horizon'undan (12→24 bar), auto-retrain
> `--horizon/--atr-mult` geçirir. Volatilite boyutlandırma: `position_size`
> `atr_ratio` çarpanı (`vol_sizing_enabled=True`, `vol_mult_hi=1.5`,
> `vol_mult_factor=0.5`); canlı sinyal üretimi + engine + backtest API + A/B
> (200 sembol: net -4,567→-4,065, maxDD iyileşti, kötüleşme yok). Plan:
> `.opencode/plans/sprint13_model_risk_plan.md`.

> Not: Sprint 14 (Geçiş Politikası + Kalibrasyon): restore yaş politikası
> (`restore_age_limit=7` gün; eski OPEN kayıtlar restart akışında
> `restore_stale_close` ile kapanır — 06.08 geçiş şoku dersi), zaman-stop
> kalibrasyonu (motor managed modda da time-stop uygular; 8s → 48s +8,074
> vs +1,736), exit kalibrasyonu (SL muli 2.0/3.0 → +10,715; TP RR kazançsız),
> AI eşik 0.55 korundu (0.50/0.60 kötü), 550 sembol kalite taraması
> (`banned_symbols` mekanizması eklendi, liste boş), `/durum` AI satırı.
> Plan: `.opencode/plans/sprint14_policy_calibration_plan.md`. Ölçümler
> docs/OPS.md'de. **Canlıya restart ile geçer** (max_position_age_hours 48,
> SL muli 2.0/3.0).

## Açık Konular (bilinen eksikler)

- **Canlı AI feedback birikimi sürüyor** (06.08: 8 kayıt; horizon 24 bar'a
  çıktığı için çözüm 10.08 00:00-04:00 UTC civarı): beklenen yakınsama ~0.62
  genel (yeni model) / son 1 ay ~0.61. `ai_auto_retrain` açık — accuracy
  tetikleyicisi `ai_retrain_min_samples=30` çözülmüş tahmin + accuracy <
  `ai_retrain_min_acc=0.55` koşulu dolunca devrede.

## Kapanan Konular (kanıtlı, kod/ölçüm kapalı)

- **Council–TTP oyu (497be93, eb82c5d)**: TTP modunda sinyal birincil oy; `decide()`
  otomatik TTP analizi; `/api/v1/market/decision|decisions` gerçek kapıyla aynı
  kararı döndürür. Canlı doğrulandı (BTCUSDT HOLD 0.18, C98USDT BUY 0.59).
- **Sinyal yoğunluğu**: `scan_limit` 100→200 (CPU ~%13, rate-limit güvenli);
  interval değişikliği (2h/30m) ölçülüp reddedildi (4h parametreleri 2h'de
  zararda, AI filtresi 30m'de ters). Döngü canlılığı: kline timeout'u (20 sn/
  istek) + websocket top 20 sınırı → döngü ~30-60 sn/döngü (eskiden ~30 dk).
- **v23 AI eşiği (070774c)**: 0.55 doğru eşik (0.50/0.45 neti düşürüyor).
- **Settings API persist (6818d78)**: REST değişiklikleri artık kalıcı.
- **Auto-retrain E2E (06.08)**: zaman tetikleyicisi → eğitim 64 sn → cache
  temizliği → yeni model restart'sız (HFTUSDT 0.7091→0.7158) + Telegram bildirimleri.
- **Sprint 13 model kalitesi**: h24 + 24 özellik kazandı (eval genel 0.625,
  son 1 ay 0.621; önceki 0.611/0.584) — `ai_direction` deploy edildi; horizon
  zinciri parametrik (settings/meta/çözümleme/retrain).
- **Sprint 13 vol boyutlandırma**: `atr_ratio > 1.5` ise risk 0.5× (canlı +
  backtest aynı yol); A/B: net -4,567→-4,065 USDT, maxDD -3.62%→-3.50%, 5
  sembol iyileşme / 0 kötüleşme.

## Veri

Backtest için `legacy/data/futures_4h_data/` (ve 30m/15m/2h) yerel
OHLCV CSV arşivi kullanılır; geri kalan eski legacy kodları
(eski XAU bot, araştırma scriptleri, TradingView stratejileri)
arşivden çıkarılmıştır (git geçmişinde mevcuttur).
