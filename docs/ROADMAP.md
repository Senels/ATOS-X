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

## Açık Konular (bilinen eksikler)

- **Council–TTP oyu (497be93, eb82c5d)**: council TTP modunda sinyalin kendisini
  birincil oy alır (v23 zorunluluğu kalktı); trend/momentum/volatilite
  oyları aynen uygulanır. `decide()` ttp modunda otomatik TTP analizi yapar —
  `/api/v1/market/decision` ve `/api/v1/market/decisions` endpoint'leri de
  gerçek kapının aynı kararını döndürür (dashboard kartı uyumlu).
- **Canlı AI feedback birikimi sürüyor** (06.08: 2 kayıt, çözüm 08.08 12:00
  UTC sonrası): beklenen yakınsama ~0.61 genel / ~0.586 son 1 ay.
  `ai_auto_retrain` açma kriteri: `ai_retrain_min_samples=30` çözülmüş tahmin
  + accuracy < `ai_retrain_min_acc=0.55`.
- **Sinyal yoğunluğu**: TTP burst'leri 4h bar kapanışlarına yakın gelir;
  pazar sessizken günlerce sinyal üretilmeyebilir → feedback yavaş birikir.
  İyileştirme (06.08): `scan_limit` **200**'e çıkarıldı (CPU ~%13, rate-limit
  güvenli; OPS.md). Interval değişikliği (2h/30m) ölçülüp **reddedildi** —
  4h parametreleri 2h'de zararda, AI filtresi 30m'de ters çalışıyor.
- **scan_limit=200**: 550+ sembolün %36'sı taranır; tam evren (550) rate-limit
  bütçesini aşar (~18 istek/sn vs 20 limit) — 200 doğru denge.
- **v23 AI eşiği doğrulandı (06.08)**: 0.50/0.45 → 26 geçen/+220 USDT, 0.55 →
  16 geçen/**+299 USDT** → `ai_min_confidence=0.55` v23 için de doğru eşik.

## Veri

Backtest için `legacy/data/futures_4h_data/` (ve 30m/15m/2h) yerel
OHLCV CSV arşivi kullanılır; geri kalan eski legacy kodları
(eski XAU bot, araştırma scriptleri, TradingView stratejileri)
arşivden çıkarılmıştır (git geçmişinde mevcuttur).
