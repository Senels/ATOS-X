# ATOS X — Operasyon ve Risk Rehberi

Bu rehber risk eşikleri, konsantrasyon engelleri, Telegram bildirimleri,
koruma (SL/TP) mekanizması ve ilgili API/dashboard görünürlüğünü belgeler.

## Risk Eşikleri

| Eşik | Varsayılan | Anlam |
|---|---|---|
| `max_position_pct` | 75 | Tek sembol projeksiyon pozisyonu (% equity) |
| `max_side_pct` | 150 | Tek yön (LONG/SHORT) toplam maruziyet (% equity) |
| `max_drawdown_pct` | 20 | Peak equity'den düşüş eşiği; aşılınca yeni girişler durur |
| `max_position_age_hours` | 8 | Pozisyonun maksimum açık kalma süresi (0 = devre dışı) |
| `max_consecutive_losses` | 5 | Ardışık zarar siniri; aşılınca yeni girişler durur (0 = devre dışı) |
| `trailing_activate_pct` | 3 | Pozisyon kârı bu eşiği aşınca SL takibi başlar (%) |
| `trailing_sl_pct` | 1.5 | Takip eden SL'nin fiyata uzaklığı (%; 0 = devre dışı) |
| `trailing_min_move_pct` | 0.1 | SL güncellemesi için gereken min hareket (%; 0 = her seferinde) |
| `breakeven_activate_pct` | 2 | Kâr bu eşiği aşınca SL giriş fiyatına taşınır (%; 0 = devre dışı) |
| `max_daily_loss_pct` | 5 | Günlük net zarar bu equity yüzdesini aşınca girişler durur (0 = devre dışı) |
| `min_equity` | 5000 | Equity bu tabanın (USDT) altına düşünce girişler durur (0 = devre dışı) |

- UI üzerinden `/dashboard/settings` → Risk sekmesinde değiştirilir.
- Canlı döngüde her taramada `_apply_risk_settings` ile uygulanır.
- Varsayılanlar `backend/app/strategy/settings.py` içindedir.

## Konsantrasyon İzleme

`AutoTrader._check_concentration` her döngüde çalışır:

- **Uyarılar**: Sembol veya yön eşiği aşıldığında bir kez
  `ATOS X UYARI` mesajı gönderilir; pozisyon eşiğin altına inip tekrar
  aşarsa yeniden uyarılır (spam yok).
- **Giriş engelleri**: `side:<LONG|SHORT>` ve `sym:<SEMBOL>` anahtarları.
  Yeni girişin projeksiyon değeri eşiği aşarsa o yönde/sembolde giriş
  engellenir. Maruziyet eşiğin altına inince engel otomatik kalkar.
- Dashboard `⚠️ Risk Exposure` kartında LONG/SHORT yüzdeleri ve aktif
  engeller chip olarak görünür.

## Drawdown Koruması

`AutoTrader._check_drawdown` her döngüde çalışır:

- `peak_equity` izlenir (her yeni zirvede güncellenir).
- `max_drawdown_pct` eşiği aşılınca `risk_halted = True` olur, Telegram
  uyarısı gönderilir ve yeni girişler engellenir (açık pozisyon yönetimi
  devam eder).
- Drawdown eşiğin **yarısına** inince otomatik serbest bırakılır
  (histerezis, flap'ı önler).
- Dashboard Risk Exposure kartında `Drawdown %` değeri ve durma durumunda
  `RISK HALT` rozeti gösterilir.

## Pozisyon Yaşı (Time-Stop)

`check_positions` her döngüde pozisyon yaşını kontrol eder:

- `max_position_age_hours` sınırını aşan pozisyon `time_stop` nedeniyle
  otomatik kapatılır (SL/TP beklentisi yoksa sermaye boşa bağlanmaz).
- `0` değeri özelliği devre dışı bırakır.
- Kapanış `reason` olarak iş geçmişine ve Telegram trade bildirimine yansır.

## Telegram Kill-Switch

Dinleyici aracılığıyla uzaktan acil durum kontrolü:

- `/durdur` — motoru durdurur; **tüm açık pozisyonları** `emergency_stop`
  nedeniyle kapatır. `running` bayrağı `False` olur, dashboard'da
  `TRADING OFF` rozeti görünür.
- `/durdur` sonrası Telegram'a kapanış özeti gönderilir: kapanan pozisyon
  sayısı, kar/zarar dağılımı, gerçekleşen net PnL ve en iyi/en kötü
  pozisyon (pozisyon yoksa `Kapanan pozisyon: 0`).
- `/kapat <SEMBOL>` — tek pozisyonu güncel fiyattan `manual_close`
  nedeniyle kapatır; sembol açık değilse ya da fiyat yoksa iptal edilir.
- `/ac` — motoru yeniden başlatır (sembolleri yükler, pozisyonları
  uzlaştırır, çevrimi döndürür).
- `/durum` — motorun `calisiyor`/`DURDURULDU` durumunu gösterir.
- `/health`, `/api/v1/status` ve `/dashboard/metrics` yanıtlarında
  `trading` alanı motor durumunu yansıtır.

## Trailing Stop

`check_positions` her döngüde pozisyon kârını değerlendirir:

- Pozisyon `trailing_activate_pct` kârını aşınca SL, fiyatın
  `trailing_sl_pct` kadar gerisinde durur ve yalnızca kâr yönünde hareket
  eder (geri çekilmez).
- SL, `trailing_min_move_pct` kadar mesafe değişmedikçe güncellenmez;
  bu, her döngüde exchange emri iptal/yerleştirme spam'ını önler.
- Exchange'te eski SL algo emri iptal edilip yenisi yerleştirilir; kağıt
  modda yalnızca bellekteki SL güncellenir.
- Takibe giren pozisyonlar dashboard'da `TRAILING` rozeti, Telegram
  `/pozisyon` yanıtında `+ TRAILING` ile işaretlenir.
- Trailing ile kapanan pozisyonların trade geçmişi `trailing: true`
  taşır; dashboard Trade History tablosunda `TRAIL` rozeti gösterilir.
- İki ayar da `0` ise özellik devre dışıdır.

Trailing/breakeven bayrakları DB `trades` tablosunda (OPEN kayıtta)
saklanır; restart sonrası `reconcile_positions` bayrakları geri yükler.
Böylece pozisyon yeniden başlatmada takip/breakeven durumunu kaybetmez.

## Break-Even Koruması

`breakeven_activate_pct` kârı aşıldığında SL giriş fiyatına taşınır
(risk olayı `breakeven_move`); fiyat geri dönerse pozisyon zarar etmeden
kapanır. SL zaten daha iyiyse (trailing devrede) dokunulmaz.
`0` = devre dışı. Dashboard'da `BREAKEVEN`, Telegram `/pozisyon`
yanıtında `+ BREAKEVEN` rozeti gösterilir.

## Risk Olay Geçmişi

`risk_events` halka tamponu (son 200) risk ve blok olaylarını kaydeder:

- `drawdown_halt` / `drawdown_clear` — drawdown eşiği aşıldı/serbest.
- `block_add` / `block_remove` — konsantrasyon engelleri.
- `trailing_activate` / `trailing_move` — trailing SL'nin devreye girmesi ve
  her SL kaydırması.
- `system_stop` — motor durdurma.

`/api/v1/risk/events` tüm kayıtları, `/dashboard/metrics` son 10 kaydı
döndürür; dashboard'da `Risk Events` kartında gösterilir. Endpoint
`limit` ve `type` (ör. `?type=drawdown_halt`) filtrelerini destekler.
`/durum` Telegram yanıtı risk olay sayısını ve son olay tipini içerir.

Risk olayları DB'deki `risk_events` tablosuna kalıcı yazılır; motor
yeniden başladığında son 200 olay bellek tamponuna geri yüklenir
(geçmiş restart'ta korunur).

Kapanan işlemler de `trades` tablosunda tutulur; yeniden başlatmada son
200 işlem `trade_history`'ye yüklenir. Böylece ardışık zarar sayacı ve
`loss_halted` durumu restart sonrası korunur ve dashboard Trade History
boş kalmaz. Kapanış nedeni (`stop_loss`, `take_profit`, `time_stop` vb.)
de DB'de `reason` sütununda saklanır.

`/ac` (veya başlatma) sonrası `_notify_startup_state` Telegram'a tam bir
özet gönderir: risk eşikleri, drawdown durumu, pozisyon yaşı, trailing
ayarları, aktif engeller ve son risk olayı.

## Ardışık Zarar Koruması

`max_consecutive_losses` kadar ardışık zarar sonrası `loss_halted`
aktifleşir ve yeni girişler engellenir (telegram uyarısı + risk olayı
`loss_streak_halt`). Bir kar (pnl >= 0) seriyi kırar, koruma otomatik
kalkar (`loss_streak_clear`). `0` = devre dışı.

Dashboard `LOSS HALT` rozeti, `/durum` yanıtı `Ardisik zarar: N/esik`
satırı, `/health`, `/api/v1/status` ve `/dashboard/metrics` ise
`loss_halted` ve `consecutive_losses` alanlarını döndürür.

## Günlük Zarar Koruması

`max_daily_loss_pct`, `_record_closed_position` anındaki equity'nin %'si
olarak günlük net zarar sınırı belirler. Sınır aşılınca `daily_loss_halted`
aktifleşir, yeni girişler engellenir (telegram uyarısı + risk olayı
`daily_loss_halt`). Yeni gün UTC'ye göre başlayınca sayaç sıfırlanır ve
koruma otomatik kalkar (`daily_loss_clear`). `0` = devre dışı.

Dashboard `DAILY HALT` rozeti, `/durum` yanıtı `Gunluk zarar: N USDT |
Gunluk durma` satırı, `/health`, `/api/v1/status` ve `/dashboard/metrics`
ise `daily_loss_halted` ve `day_pnl` alanlarını döndürür.

## Gerçekleşmemiş PnL

Açık pozisyonların anlık kâr/zararı `live_prices` üzerinden hesaplanır:

- `/pozisyon` yanıtı her pozisyon için `PnL: N (+%P)` ekler; varsa `SL: $` ve `TP: $` fiyatları gösterilir.
- `/kapat <SEMBOL>` tek pozisyonu güncel fiyattan kapatır (neden
  `manual_close`); sembol açık değilse veya fiyat yoksa işlem iptal edilir.
- `/api/v1/positions` her pozisyon için `mark`, `upnl`, `upnl_pct` ve
  toplam için `total_upnl` döndürür.
- Dashboard Aktif Pozisyon tablosunda PnL sütunu (kâr yeşil, zarar kırmızı).
- `/durum` yanıtı toplam gerçekleşmemiş PnL satırı içerir.

Fiyat yoksa (`live_prices` boş) alanlar `null` gelir.

## Equity Taban Koruması

`min_equity` (USDT) mutlak alt sınırdır. Equity bu değerin altına
düşünce `equity_halted` aktifleşir, yeni girişler engellenir (telegram
uyarısı + risk olayı `equity_floor`). Equity taban sınırın üzerine
dönünce koruma otomatik kalkar (`equity_clear`). `0` = devre dışı.

Dashboard `EQUITY FLOOR` rozeti, `/durum` yanıtı `Equity taban: $N |
Taban durma` satırı, `/health`, `/api/v1/status` ve `/dashboard/metrics`
ise `equity_halted` ve `min_equity` alanlarını döndürür.

## Backtest Risk Simülasyonu

Backtest motoru artık canlı ayarlarla aynı risk kurallarını simüle eder
(parametreler `BacktestEngine`'e geçirilir, `/api/v1/backtest` sonuçları
canlı ayarları kullanır):

- **Drawdown halt** — peak equity'den %`max_drawdown_pct` düşüşte yeni
  girişler engellenir (histerezis: yarısına dönünce serbest).
- **Ardışık zarar** — %`max_consecutive_losses` ardışık kayıp sonrası
  girişler engellenir, kar seriyi kırar.
- **Breakeven + trailing SL** — kâr eşiği aşılınca SL girişe taşınır /
  takip edilir (SL yalnızca min hareket aşılınca güncellenir).
- **Time-stop** — `max_position_age_hours` aşılan bar sayısı (interval'e
  göre saat) sonrası pozisyon kapanır.
- **Equity taban** — `min_equity` altında girişler engellenir.

Tüm parametreler varsayılan olarak `0` = devre dışı; yalnızca canlı
ayarlar sıfırdan farklıysa davranış değişir. Risk parametreleri
`metrics.params` içinde saklanır.

`/api/v1/backtest` risk ayarlarını opsiyonel query parametreleriyle ezme
imkanı verir: `max_drawdown_pct`, `max_consecutive_losses`,
`max_daily_loss_pct`, `min_equity`, `trailing_activate_pct`,
`trailing_sl_pct`, `trailing_min_move_pct`, `breakeven_activate_pct`,
`max_position_age_hours`. Verilmezse canlı ayarlar kullanılır. Grid
search (`GridSearch`) da varsayılan olarak aynı risk ayarlarıyla çalışır.

`/backtest/html` web arayüzü backtesti çalıştırır: sembol, interval,
limit, kaynak (CSV/Binance), strateji override'ları (leading indicator,
signal expiry, RR, SL lookback, ATR, confirmations) ve motor/risk
override'ları. Sonuç metrikleri (return, net profit, win rate, PF,
Sharpe, max DD, exposure) + equity eğrisi + işlem tablosu gösterilir;
geçmiş çalışmalar tablosundan kayıt yeniden yüklenebilir. Boş bırakılan
risk alanları canlı ayarları kullanır.

Geçmiş çalışmalar tablosunda iki kayıt işaretlenip **Karsilastir** ile
`/api/v1/backtest/compare` çağrılır; metrikler yan yana tabloda
gösterilir ve daha iyi olan (getiride yüksek, DD'de düşük) yeşil
işaretlenir.

## Parametre Optimizasyonu (Grid Search)

`GridSearch` (ProcessPoolExecutor destekli) TradeBotV23 + BacktestEngine
üzerinde grid arama yapar; her kombinasyon tüm sembollerde değerlendirilip
ortalama skora göre sıralanır.

- `/api/v1/optimize` — grid arama çalıştırır. Sorgu parametreleri:
  - `symbols` (varsayılan `BTCUSDT,ETHUSDT`), `interval`, `limit`,
    `objective` (`combined` | `return` | `sharpe` | `pf`), `max_workers`.
  - Grid boyutları virgüllü listelerle ezilir: `rangefilt_length`,
    `range_filt_mult`, `signal_expiry`, `rr_ratio`, `sl_lookback`
    (boş bırakılırsa varsayılan grid kullanılır).
  - `save_to_file=true` → en iyi kombinasyon
    `optimized_settings.json` dosyasına yazılır.
  - Yanıt: `results` (sıralı), `best`, `grid`, `symbols`, `interval`,
    `limit`, `objective`.
- `/api/v1/optimize/defaults` — varsayılan grid ve objective seçenekleri.
- `/optimize/html` — parametre optimizasyonu web arayüzü (grid boyutları,
  semboller, objective, dosyaya kaydet; sonuçlar detay tablosu).
- `/api/v1/optimize/apply` (POST) — kayıtlı en iyi kombinasyonu
  (`optimized_settings.json`) canlı ayarlara uygular ve `settings.json`'a
  kalıcı yazar. `rangefilt_length`, `range_filt_mult`, `signal_expiry`,
  `rr_ratio`, `sl_lookback` anahtarlarını uygular; dosya yoksa `404`.
- Arama, canlı ayarlardaki risk parametrelerini `BacktestEngine`'e geçirir;
  risk simülasyonu backtest ile aynıdır.

## Pozisyon Yaşı ve Yeniden Başlatma

`/ac` (veya motor yeniden başlatma) sonrası `reconcile_positions` geri
yüklenen pozisyonların gerçek açılış zamanını `trades.entry_time`
kaydından okur. Böylece time-stop (pozisyon yaşı) yeniden başlatmada
sıfırlanmaz; yaşı aşan pozisyonlar yine `time_stop` ile kapanır.
DB'de OPEN kaydı yoksa açılış zamanı şimdi kabul edilir.

## Risk Durumu Kalıcılığı (Restart Dayanıklılığı)

Runtime risk durumu `app_state` tablosunda kalıcıdır; motor restart
edildiğinde geri yüklenir. Kalıcı alanlar: `equity`, `peak_equity`,
`drawdown_pct`, `day_pnl`, `day_start_date`, `consecutive_losses` ve
halt bayrakları (`risk_halted`, `daily_loss_halted`, `equity_halted`).

- **Deterministik yeniden türetim**: `loss_halted` her restart'ta kapanan
  işlem geçmişinden, `equity_halted` ise `equity` vs `min_equity`'den
  yeniden hesaplanır (bayrak birebir geri yüklenmez).
- **Gün değişimi**: Kayıtlı `day_start_date` dünden ise `day_pnl` ve
  günlük zarar koruması sıfırlanır; aynı gün ise aynen korunur.
- **Yazma noktaları**: pozisyon açma/kapama, ardışık zarar güncellemesi,
  günlük PnL, drawdown ve equity taban kontrolleri ile periyodik
  `update_equity` her değişimde durumu DB'ye yazar. Böylece gün ortasında
  restart yapılsa da günlük zarar/peak equity korumaları sıfırlanmaz.

## WebSocket Canlı Fiyat Aboneliği

- Başlangıçta 5 temel sembol (`BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`,
  `ADAUSDT`) `@trade` stream'ine abone olunur.
- Arka planda `_ws_sync_loop` (60 sn) abonelik setini tarama listesine
  (`top_symbols`) hizalar: listede yeni çıkan semboller bağlanır, düşenler
  kapatılır. `top_symbols` boşsa mevcut abonelikler korunur.
- Kaldırılan sembolün bağlantısı bilinçli kapatılır ve yeniden bağlanmaz
  (`_removed`); `stop` tüm bağlantıları aynı şekilde kapatır.

## Telegram Bildirimleri

- **Başlangıç**: `_notify_startup_state` risk eşiklerini ve mevcut
  engelleri bildirir.
- **Durum değişimi**: `_sync_block_state` engel seti değişince
  eklendi/kaldırıldı özetini gönderir (değişiklik yoksa sessiz).
- **Periyodik özet**: Engeller aktifken saatte bir
  `N konsantrasyon engeli aktif: ...` hatırlatması.
- **Koruma**: Kayıp SL/TP algo emri borsada yoksa tamir edilir;
  edilemezse `ATOS X UYARI` ile manuel müdahale çağrısı yapılır.
- **Günlük rapor**: `DAILY_REPORT_HOUR`'da özet rapor gönderilir. Rapor;
  kapanan işlem (W/L), win rate, profit factor, günlük PnL, günlük net
  (kapanan), gerçekleşmemiş PnL, en iyi işlem, en iyi sembol, per-sembol
  dağılım (ilk 5), açık pozisyon sayısı, CSV veri tazeliği
  (guncel/eski/eksik), trailing/breakeven koruma sayıları, aktif durmalar
  (ardışık/günlük zarar, equity taban) ve son risk olayını içerir.

## Telegram Komutları

Bot, long-polling (`getUpdates`) ile komutları dinler. Token/chat_id
yoksa dinleyici devre dışıdır.

| Komut | Yanıt |
|---|---|
| `/durum` veya `/status` | Equity, pozisyon özeti (korumalı/korumasız, trailing/breakeven), LONG/SHORT maruziyeti, engeller, drawdown, zincir/günlük/equity taban durmaları, UPnL ve yaş uyarıları |
| `/blok` | Aktif konsantrasyon engelleri (veya `yok`) |
| `/pozisyon` | Her pozisyon: sembol, taraf, qty, fiyat + `korumali`/`KORUMASIZ`, varsa SL/TP fiyatları, gerçekleşmemiş PnL ve pozisyon yaşı (saat) |
| `/kapat <SEMBOL>` | Tek pozisyonu manuel kapatır (canlı fiyat ile) |
| `/sl <SEMBOL> <FIYAT>` | Açık pozisyonun SL'sini günceller; `/sl all <FIYAT>` tüm pozisyonlar için toplu, `/sl all %P` giriş ile mevcut fiyat arasındaki mesafenin %'si, `/sl breakeven` tüm SL'leri giriş fiyatına taşır |
| `/tp <SEMBOL> <FIYAT>` | Açık pozisyonun TP'sini günceller (SL korunur); `/tp all <FIYAT>` tüm pozisyonlar için toplu, `/tp all %P` giriş ile mevcut fiyat arasındaki mesafenin %'si |
| `/kapatall` | Açık tüm pozisyonları kapatır; onay gerektirir: `/kapatall` → onay isteği, `/kapatall onay` → kapatır. Sembol listesi desteği: `/kapatall BTCUSDT,ETHUSDT onay` yalnızca belirtilen pozisyonları kapatır; belirtilen sembollerde açık pozisyon yoksa işlem iptal edilir |
| `/koruma`, `/ayar` veya `/ayarla` | Risk eşiklerini gösterir; `/koruma <anahtar> <deger>` ile canlı değiştirir (kalıcı) |
| `/durdur` veya `/stop` | Acil durdurma; tüm pozisyonları kapatır + kapanış özeti gönderir; onay gerektirir: `/durdur` → onay isteği, `/durdur onay` → çalıştırır |
| `/sinyal <SEMBOL>` veya `/signal <SEMBOL>` | Canlı kline'dan v23 sinyalini Telegram'a gönderir; güç (aktif konfirmasyon / toplam konfirmasyon) yüzdesi dahil |
| `/sinyalall [N] [INTERVAL] [BUY/SELL/HOLD]` veya `/scan [N] [INTERVAL]` | İlk N (varsayılan 5, en fazla 10) tarama sembolü için toplu sinyal özeti; interval opsiyonel (`15m`…`1d`, varsayılan `4h`), sinyal tipi filtresi ve her satırda `guc:%` dahil |
| `/ac` veya `/resume` | Motoru yeniden başlatır |
| `/rapor [GUN]` veya `/report [GUN]` | Günlük raporu anında gönderir; `GUN` opsiyonel (varsayılan 1, en fazla 90) geriye dönük pencereyi belirler |
| `/risk` | Risk durumu: equity, LONG/SHORT maruziyeti, drawdown, ardışık/günlük zarar, equity tabanı, tüm durma durumları ve **pozisyon bazlı risk dağılımı** (her pozisyon için notional, SL mesafe %, risk $) + toplam notional (% equity) |
| `/islem` | Bugün kapanan işlemlerin listesi: sembol, taraf, çıkış fiyatı, PnL + Toplam PnL ve W/L özeti |
| `/bekleyen` | Borsadaki bekleyen SL/TP algo emirlerini sorgular ve listeler (`bekleyen emir yok` veya sembol bazlı `SL`/`TP` satırları) |
| `/bakiye` | Bakiye özeti: equity, gerçekleşmemiş PnL, toplam, pozisyon sayısı (L/S), günlük PnL, drawdown ve açık pozisyonların satır satır PnL/yaş bilgisi |
| `/alarm <SEMBOL> <FIYAT> [ust/alt]` | Fiyat alarmı ekler; `ust` (varsayılan) fiyat eşiğin üstüne çıkınca, `alt` altına inince Telegram bildirir. `/alarm` aktif alarmları listeler, `/alarm temizle` hepsini siler. Alarmlar her 20 sn'de kontrol edilir; tetiklenen alarm otomatik silinir |
| `/gecmis [N] [SEMBOL]` | Son N kapanış işlemi (varsayılan 5, en fazla 20); sembol verilirse sadece o sembolün işlemleri gösterilir; üstte pencere özeti (Net, Kazanma %, PF) |
| `/istatistik` veya `/stats` | Tüm geçmişin özeti: işlem sayısı, Net PnL, Kazanma %, Profit Factor, ortalama kar/zarar, en iyi sembol ve trailing/breakeven koruma istatistikleri |
| `/veri` veya `/data` | CSV veri tazeliği özeti (Guncel/Eski/Eksik) + eski/eksik semboller |
| `/backfill [SEMBOLLER] [GUN]` | Eski/eksik CSV verisini tazeler; sembol verilmezse otomatik `eski/eksik` seçilir (varsayılan 30 gün, en fazla 90) |
| `/temizle [hepsi]` | Kapanan işlem geçmişini siler (in-memory + DB); `hepsi` eklentisi sinyal/backtest/risk/performans tablolarını da boşaltır. Açık pozisyonlar ve ayarlar korunur |
| `/izleme [N]` | Öncelik listesinin canlı skor sıralaması (varsayılan 10, en fazla 20); her sembol için skor, momentum % ve trend göstergesi (🟢/🔴/⚪) |
| `/performans` | Equity curve özeti: equity, peak, drawdown %, kazanma oranı ve son 6 aylık istatistik (işlem sayısı, net PnL, kazanma %) |
| `/son` | Son kapanan işlemin detayı: sembol, yön, giriş/çıkış fiyatı, PnL, neden, koruma (trailing/breakeven) ve zaman |
| `/yardim` veya `/help` | Komut listesi |

## Fiyat Alarmları

`/alarm` komutuyla canlı fiyat eşikleri tanımlanır; arka plandaki
`_alarm_loop` her 20 sn'de bir `live_prices` üzerinden kontrol eder:

- `/alarm <SEMBOL> <FIYAT>` — sembol eşiğin **üstüne** çıkınca bildirir.
- `/alarm <SEMBOL> <FIYAT> alt` — eşiğin **altına** inince bildirir.
- `/alarm` — aktif alarmları listeler; `/alarm temizle` hepsini siler.
- Zaten eşiğin üstünde/altında olan fiyata alarm eklenmez (anında tetikleme
  yok); tetiklenen alarm otomatik olarak listeden çıkar.
- Alarmlar **kalıcıdır**: `/alarm` eklenince `price_alerts` tablosuna yazılır,
  tetiklenince veya `/alarm temizle` ile silinir. Motor restart'ında
  `_load_alarms` DB'den geri yükler (restart'ta kaybolmaz).
- Aynı `(symbol, price, side)` kombinasyonundan ikinci kez eklenemez
  (`zaten mevcut`); DB tarafında `PRIMARY KEY` ile idempotent.
- Motor durmuşsa kontrol yapılmaz.

## Sinyal Gücü (Strength)

`TradeBotV23._confirmations` artık aktif konfirmasyon sayısını da üretir;
`generate_signal` çıktısına `strength` (0-1) ekler. Güç = sinyal yönüyle
uyumlu aktif konfirmasyon / toplam açık konfirmasyon oranıdır (HOLD → 0).

- `/sinyal <SEMBOL>` ve `/sinyalall` mesajlarında `Guc: %N` olarak gösterilir.
- `/api/v1/signals` her sinyal için `strength` alanı döndürür.
- Güç yalnızca görsel bilgidir; giriş kararını doğrudan değiştirmez.

## Koruma (SL/TP)

- Pozisyon açılışında borsaya algo STOP_MARKET / TAKE_PROFIT_MARKET
  emirleri yerleştirilir; kimlikleri `sl_order_id` / `tp_order_id`
  olarak saklanır.
- `reconcile_positions` (5 dk) borsa gerçeğiyle hizalar:
  - Borsada karşılığı olmayan pozisyonları kapatır.
  - İzlenen pozisyonda kayıp emir varsa yeniden yerleştirir.
  - Korumasız pozisyon tespit edilirse uyarır.
- Korumalı = en az bir (SL veya TP) algo emir kimliği mevcut.

**Manuel SL güncelleme** (`/sl <SEMBOL> <FIYAT>` → `AutoTrader.update_sl`):
- Yön korunur: BUY pozisyonunda SL giriş fiyatının altında, SELL'de üstünde
  olmalı; ihlal eden istek reddedilir.
- Borsadaki eski SL algo emri iptal edilip yeni SL yerleştirilir, TP korunur
  (`set_tp_sl` ile sadece SL yenilenir). Paper modda sadece kayıt güncellenir.
- Manuel müdahale sonrası trailing/breakeven bayrakları sıfırlanır (DB'ye
  yazılır); otomatik koruma sonraki döngüde yeniden devreye girebilir.
- İşlem `risk_events`'e `manual_sl_update` olarak kaydedilir.
- Toplu varyantlar: `/sl all <FIYAT>` tüm SL'leri sabit fiyata çeker,
  `/sl all %P` giriş-mevcut-fiyat mesafesinin %'sini SL olarak verir
  (BUY'da giriş + mesafe·%, SELL'de giriş − mesafe·%),
  `/sl breakeven` tüm SL'leri giriş fiyatına taşır.

**Manuel TP güncelleme** (`/tp <SEMBOL> <FIYAT>` → `AutoTrader.update_tp`):
- Yön korunur: BUY pozisyonunda TP giriş fiyatının üstünde, SELL'de altında
  olmalı; ihlal eden istek reddedilir.
- Borsadaki eski TP algo emri iptal edilip yeni TP yerleştirilir, SL korunur
  (`set_tp_sl` ile sadece TP yenilenir). Paper modda sadece kayıt güncellenir.
- İşlem `risk_events`'e `manual_tp_update` olarak kaydedilir.

**Canlı risk eşiği değiştirme** (`/koruma <anahtar> <deger>`):
- `strat_settings.update_settings` + `persist()` ile `settings.json`'a kalıcı
  yazılır ve `_apply_risk_settings` bir sonraki döngüde motora uygular.
- Düzenlenebilir anahtarlar: `max_positions`, `max_drawdown_pct`,
  `max_consecutive_losses`, `max_daily_loss_pct`, `min_equity`,
  `risk_per_trade`, `max_position_pct`, `max_side_pct`,
  `trailing_activate_pct`, `trailing_sl_pct`, `trailing_min_move_pct`,
  `breakeven_activate_pct`, `max_position_age_hours`, `max_leverage`,
  `use_decision_council`, `council_min_confidence`, `min_signal_strength`,
  `use_score_ranking`,
  `data_backfill_hours`, `data_freshness_hours`.
- `/koruma` (parametresiz) mevcut değerleri ve anahtar listesini gösterir.

**Dashboard'dan pozisyon SL/TP düzenleme ve kapatma:**
- Aktif Pozisyon tablosunda her satırda SL/TP input alanları, `Uygula` ve
  `Kapat` butonları vardır.
- `Uygula` yalnızca dolu alanları `/api/v1/positions/<SEMBOL>/sl` ve
  `/api/v1/positions/<SEMBOL>/tp` uçlarına gönderir; sonuçlar `AutoTrader.update_sl` /
  `update_tp` ile aynı yön doğrulamasından geçer (hata durumunda alert).
- `Kapat` onay sonrası `/api/v1/positions/<SEMBOL>/close` ile pozisyonu canlı
  fiyatla kapatır (`live_prices` → `get_all_tickers` fallback; fiyat bulunamazsa
  işlem reddedilir) ve `AutoTrader.close_position` ile SL/TP algo emirlerini iptal eder.
- Uçlar `not_running`, `position_not_found`, `price_not_found` hataları döndürür.

## Market Intelligence (Rejim / Volatilite)

- `app/strategy/market_intel.py` kline DataFrame'inden deterministik rejim
  sinyalleri üretir (`analyze`):
  - **Volatilite**: ATR%'nin son 100 bar içindeki yüzdelik dilimi → `LOW`
    (<%30), `NORMAL` (%30-70), `HIGH` (%70-90), `EXTREME` (>=%90). Sabit ATR
    (`std ≈ 0`) `NORMAL`/%50 sayılır.
  - **Trend**: hızlı (21) / yavaş (50) EMA hizası + son 100 bar eğimi →
    `UP`, `DOWN`, `RANGE`.
  - **Likidite**: son 20 bar ortalama hacim + z-skoru (proxy).
- Uçlar: `/api/v1/market/regime?symbol=&interval=` (tek sembol),
  `/api/v1/market/regimes?limit=&interval=` (tarama listesi, dashboard kartı).
- Dashboard `🌡️ Market Regime` kartında trend/volatilite rozetleri, ATR% ve
  ATR%ile gösterilir; `📡 Live Signals` ile aynı interval seçimini kullanır.
- Çıktı ilerleyen batch'lerde risk boyutlandırma ve Decision Council'a girdi olacaktır.

## Coin Intelligence (Momentum/Score)

- `app/strategy/coin_intel.py` sembol seçimi için bileşik skor üretir
  (`coin_score`): ağırlıklı momentum (r20 %40, r10 %30, r5 %20, r1 %10) +
  trend rejimi skoru (UP +1 / RANGE 0 / DOWN -1, katsayı 2) - volatilite
  cezası (HIGH -0.5, EXTREME -1.0).
- Skor yetersiz veride 0 döner (`reason: yetersiz veri`).
- Uç: `/api/v1/market/scores?limit=&interval=` tarama listesini skora göre
  azalan sıralar.
- Dashboard `🏆 Coin Scores` kartında skor (pozitif yeşil/negatif kırmızı),
  momentum %, trend rozeti ve ATR% gösterilir; interval seçimini Live Signals
  ile paylaşır.
- İlerleyen batch'te skor, `auto_trader` sembol seçimine (top_symbols) bağlanabilir.

## Decision Council (Çoklu Sinyal Oylaması)

- `app/strategy/decision.py` v23 + trend + momentum + volatilite kapısını
  oylayarak BUY/SELL/HOLD kararı ve güven (confidence) üretir; `votes` listesi
  kararın kaynaklarını açıklar (`_vote` saf fonksiyon, `decide` DataFrame sarmalar).
- Ağırlıklar: v23=1.0, trend=0.4, momentum=0.3 (net max 1.7). Net >= 0.8 → BUY,
  <= -0.8 → SELL, aksi HOLD. HIGH volatilite -0.3 ceza; EXTREME hard veto (HOLD).
- Tek başına v23 sinyali tetikleyebilir ama güven düşer; rejim/momentum aynı yönde
  olduğunda güven 1.0'a yaklaşır, zıt yönde HOLD.
- Uçlar: `/api/v1/market/decision?symbol=&interval=` (tek), `/api/v1/market/decisions`
  (tarama listesi, BUY/SELL/HOLD + confidence sıralı). Dashboard `⚖️ Decision Council`
  kartı kararları ve kaynaklarını gösterir.

**Canlı döngüye bağlantı** (`AutoTrader._council_gate`):
- `use_decision_council=True` ise taramadaki her BUY/SELL sinyali council'den
  geçer; council kararı sinyal yönünde değilse veya güven
  `council_min_confidence` (varsayılan 0.6) altındaysa giriş engellenir.
- Kabul edilen sinyale `council_confidence` / `council_reason` eklenir.
- Varsayılan kapalıdır (operatör `/koruma use_decision_council 1` ile açar).
- `/koruma` editörüne `use_decision_council` (bool 1/0) ve
  `council_min_confidence` (0-1) eklendi.

## Min Sinyal Gücü (`min_signal_strength`)

- `generate_signal` sinyal gücünü (strength) aktif konfirmasyon oranı olarak
  üretir: `n_active / n_total` (0-1). BUY için long, SELL için short
  konfirmasyon sayısı, toplam etkin konfirmasyon sayısına bölünür.
- `AutoTrader._strength_gate`: eşik `> 0` iken strength eşiğin altında olan
  BUY/SELL sinyalleri girişe alınmaz; `low_signal_strength` tipinde risk
  olayı kaydedilir (`/api/v1/risk/events` + DB, Telegram `/risk` ile görünür).
- Backtest motoru `BacktestEngine(min_signal_strength=...)` aynı eşiği uygular
  (`analyze()` çıktısındaki `strength` sütunu; sütun yoksa eşik geçmez —
  eski veriyle uyumlu). `/backtest?min_signal_strength=0.6` ve backtest
  sayfasındaki "Min Signal Strength (0-1)" alanıyla test edilebilir.
- Optimizasyon, `engine_kwargs` üzerinden canlı eşiği kullanır.
- Varsayılan `0.0` (kapalı); `/koruma min_signal_strength 0.6` veya Dashboard
  Settings → Risk → "Min Signal Strength (%)" ile açılır.
- Telegram sinyal bildirimi açılan pozisyonun gücünü "Guc: %N" satırıyla gösterir.

## Market Collector (Veri Toplama / Backfill)

- `app/data/collector.py` Binance kline'larını `legacy/data/futures_{interval}_data/`
  klasörüne `loader.load_csv` uyumlu CSV olarak yazar (timestamp[ms], OHLCV).
- `collect`: sembollerin güncel kline'larını çeker/yazar; stablecoin sembolleri
  atlar; hatayı `failed` listesine işler.
- `backfill`: `get_klines(..., start_time=...)` ile geçmişe doğru parçalı çekim
  yapar, tekrar eden bar'lar `drop_duplicates` ile ayıklanır, sıralı yazılır.
- `BinanceClient.get_klines` artık opsiyonel `start_time` (ms) kabul eder.
- Uçlar: `POST /api/v1/data/collect` (`symbols=`,`interval=`,`bars=`) ve
  `POST /api/v1/data/backfill` (`symbols=`,`interval=`,`days=`);
  sembol verilmezse ilk 10 tarama sembolü kullanılır.
- `GET /api/v1/data/status` (`limit=`): ilk 100 (en fazla 300) sembolün CSV
  tazeliğini `data_freshness_hours` eşiğiyle `ok`/`stale`/`missing` olarak
  listeler; `fresh`/`stale`/`missing` toplamlarıyla döner.
- `/veri` Telegram komutu: veri durumu özetini (Guncel/Eski/Eksik + eski ve
  eksik sembol listesi) gösterir; otomatik backfill eşiğiyle birebir uyumludur.

## Portföy Senkronu (Sprint 10)

- `BinanceClient.get_account_balance()`: futures hesap özeti
  (`totalWalletBalance` / `availableBalance` / `totalUnrealizedProfit`).
- `AutoTrader._sync_balance()`: `reconcile_positions` başında canlı dengeden
  iç `equity`'yi hizalar (`balance + unrealized`), `peak_equity`/`drawdown_pct`
  günceller ve kalıcı risk durumunu yazar; borsa yöntemi yoksa/geçersiz
  değerde sessizce atlanır (test uyumluluğu).
- `reconcile_positions`: borsada açık takipli pozisyonda SL/TP fiyatı tanımlı
  ama karşılık gelen algo emri borsada yoksa koruma yeniden yerleştirilir
  (sadece SL/TP fiyatı 0 olmayanlar; fiyatsız pozisyonlar sessizce atlanır).

## Canlı Sembol Seçimi (Score Ranking)

- `AutoTrader._rank_by_score()`: `use_score_ranking=True` iken `_refresh_ranking`
  ilk 200 sembolün canlı 4h kline'larını çeker, `coin_score` ile momentum
  skoru hesaplar ve listeyi skor azalan şekilde yeniden sıralar; skoru
  alınamayan semboller backtest sıralamasını koruyarak arkada kalır.
- Varsayılan **kapalı**; `/koruma use_score_ranking 1` ile açılır
  (`_BOOL_RISK_KEYS`'e eklendi, `/koruma` çıktısında `Skor siralamasi` satırı).
- `_SCORE_POOL = 200` havuz boyutunu belirler.
- Ayar editörü (`/dashboard/settings` → Risk sekmesi): Decision Council
  toggle, Council Min Confidence (%) ve Score Ranking toggle'ları eklenmiştir
  (`saveAll`/`loadDefaults`/`refresh` ile eşleşir).
- Otomatik backfill: `data_backfill_hours` (0 = kapalı) arayla top 100
  sembolün CSV'si tazelik kontrol edilir; eksik ya da son bari
  `data_freshness_hours` (varsayılan 12) saatten eski olanlar
  `backfill_klines` ile tazelenir. `/koruma data_backfill_hours 6` ile açılır.
- `GET /api/v1/portfolio`: mode (paper/live), `synced` bayrağı, balance,
  available, unrealized PnL, equity/peak/drawdown, day PnL ve pozisyon
  bazlı notional/uPnL listesi.
- Dashboard `💼 Portfolio` kartı: equity, available, uPnL, drawdown, day PnL,
  toplam maruziyet.

## API ve Dashboard Görünürlüğü

| Uç | İçerik |
|---|---|
| `/health` | `protected_positions`, `concentration` |
| `/api/v1/status` | `protected_positions`, `concentration`, pozisyon sayısı |
| `/api/v1/positions` | Pozisyon başına `protected` bayrağı + `protected`/`unprotected` sayıları |
| `/api/v1/risk/positions` | Pozisyon bazlı risk: `notional`, `size_pct`, `sl_distance_pct`, `risk_amount`, `upnl`, `protected`/`trailing`/`breakeven`, `age_hours` + toplamlar (`total_notional`, `total_risk_amount`) |
| `/api/v1/priority` | Tarama listesi (`scanned`) ve rank edilmiş semboller (`symbols`) |
| `/api/v1/signals` | Tarama listesi için canlı v23 sinyalleri (`BUY`/`SELL`/`HOLD`): `symbol`, `price`, `sl`, `tp`, `reason`, `indicator`, `strength` (0-1 konfirmasyon oranı); `limit` (varsayılan 12) ve `interval` |
| `/api/v1/performance/summary` | Performans özeti: Bugun/Haftalik/Aylik/Tum zaman için işlem sayısı, W/L, win rate, net PnL ve profit factor; dashboard `🚀 Performans` kartında gösterilir |
| `/api/v1/positions/<S>/sl` | Dashboard/API'den açık pozisyon SL güncelleme (`AutoTrader.update_sl`) |
| `/api/v1/positions/<S>/tp` | Dashboard/API'den açık pozisyon TP güncelleme (`AutoTrader.update_tp`) |
| `/api/v1/positions/<S>/close` | Açık pozisyonu canlı fiyatla kapatma (`AutoTrader.close_position`) |
| `/api/v1/market/regime` | Tek sembol rejim/volatilite/likidite tespiti (`symbol`, `interval`) |
| `/api/v1/market/regimes` | Tarama listesi için rejim özeti (`limit`, `interval`) |
| `/api/v1/market/scores` | Tarama listesi için momentum/score sıralaması (`limit`, `interval`) |
| `/api/v1/market/decision` | Tek sembol Decision Council kararı (`symbol`, `interval`) |
| `/api/v1/market/decisions` | Tarama listesi karar özeti (BUY/SELL/HOLD + confidence) |
| `/api/v1/data/collect` | Sembol kline'larını CSV arşivine toplar |
| `/api/v1/data/backfill` | Sembol geçmiş verisini parçalı çekip arşive yazar |
| `/api/v1/data/status` | CSV veri tazeliği: sembol bazında `ok`/`stale`/`missing` + toplamlar |
| `/api/v1/data/backfill/stale` | Eski/eksik sembolleri otomatik seçip backfill eder (`days=`, varsayılan 30) |
| `/api/v1/portfolio` | Portföy özeti (senkron equity, bakiye, uPnL, pozisyonlar) |
| `/dashboard/metrics` | Aynı + pozisyon başına `protected` |
| `/dashboard` | Pozisyon tablosunda `KORUMALI`/`KORUMASIZ` rozetleri + kart özeti; `🧮 Position Risk` kartında notional, size %, SL mesafesi, risk tutarı, uPnL ve pozisyon yaşı; `🚀 Performans` kartında Bugun/Haftalik/Aylik/Tum zaman metrikleri; `📡 Live Signals` kartında tarama listesinin canlı sinyalleri (60 sn'de bir yenilenir; üstten interval seçilir — varsayılan `4h`, seçim `localStorage`'da saklanır) ve sinyal tipi filtresi (Tumu/BUY/SELL/HOLD); `🌡️ Market Regime` kartında trend/volatilite rejimi + ATR%; `🏆 Coin Scores` kartında skor sıralaması; `⚖️ Decision Council` kartında kararlar + güven + kaynaklar; `🗃️ Data Freshness` kartında CSV tazeliği (guncel/eski/eksik özeti + sembol tablosu) ve `↻ Backfill` butonu (eski/eksik veriyi anında tazeler); Trade History kartında `TRAIL`/`BE` rozetleri; her satırda SL/TP düzenleme + `Uygula`/`Kapat` butonları |

## Güvenlik

`API_KEY` (`.env`) set edilmişse **tüm `/api/v1*` REST uçları** `X-API-Key`
header'ıyla korunur — eksik/yanlış anahtara `401 Unauthorized` döner
(`APIKeyMiddleware`, `app/core/security.py`). Boş bırakılırsa uçlar korumasızdır
(dev/test; canlıya geçerken mutlaka doldurun).

- `/health`, `/dashboard/html`, `/dashboard/metrics` gibi sayfa/izleme uçları
  `/api/v1` dışında kaldığı için API anahtarından etkilenmez.
- Dashboard/Settings/Backtest/Optimize sayfaları anahtarı `localStorage`
  (`atos_api_key`) üzerinden saklar ve her `/api/v1` isteğine `X-API-Key`
  header'ı ekler; `401` dönünce anahtar yeniden sorulur.
- **CORS**: `ALLOWED_ORIGINS` (virgülle ayrılmış) dış kaynaklara açılan
  originleri belirler; boşsa yalnızca `localhost`/`127.0.0.1` varsayılanıdır.
- **Telegram yetkilendirme**: `TELEGRAM_ALLOWED_CHAT_IDS` (virgülle ayrılmış)
  komut çalıştırabilecek sohbetlerin whitelist'idir. Boşsa filtre yoktur; doluysa
  whitelist dışındaki sohbetlerin mesajları sessizce atlanır (log'a yazılır) —
  botu bulan yabancılar `/durdur onay`, `/kapatall onay` gibi tehlikeli
  komutları çalıştıramaz. Grup için negatif ID (örn. `-1001234567890`) desteklenir.
- Anahtarlar yalnızca `backend/.env` içinde tutulur; git'e asla işlenmez.

Örnek `.env`:

```
API_KEY=sifreli-anahtar
TELEGRAM_ALLOWED_CHAT_IDS=123456789,-1001234567890
ALLOWED_ORIGINS=http://localhost:8000
```



`Database.backup()` SQLite **online backup API**'siyle (`sqlite3` `backup`)
çalışan süreç sırasında tutarlı bir kopya alır — dosyayı kopyalamaz, bu
yüzden yazma sırasında bozuk kopya riski yoktur.

- Yedekler DB dosyasının yanındaki `backups/` klasörüne
  `<adi>_backup_YYYYMMDD_HHMMSS_ffffff.db` adıyla yazılır.
- **Bütünlük doğrulaması**: her yedek alınırken kopya `PRAGMA integrity_check`
  ile doğrulanır; bozuk kopya silinir ve hata döner (`integrity_check_failed`).
- **Retention**: en genç 14 yedek saklanır, eskileri otomatik silinir
  (`keep` parametresiyle değiştirilebilir).
- **Periyodik**: `_backup_loop` her 6 saatte bir `app.state.db.backup()` çağırır
  (lifespan'da başlar, shutdown'da iptal edilir); `asyncio.to_thread` ile
  **bloklamadan** çalışır.
- **Hata bildirimi**: periyodik yedekleme başarısız olursa durum log'a yazılır
  ve Telegram'a `ATOS X UYARI: DB yedekleme basarisiz (...)`, ayrıca başlangıç
  ve kapanış başarısız bildirimleri gönderilir.
- **Manuel**: `/yedek` Telegram komutu anında yedek alır ve dosya yolunu
  döndürür; `/yedekler` mevcut yedekleri listeler. REST: `POST /api/v1/backup`
  anında yedek alır, `GET /api/v1/backups` yedek listesini (ad, boyut, tarih) döner.
- **Restore**: `Database.restore(yedek)` yedek dosyasını önce doğrular, mevcut
  DB'yi `pre_restore_<zaman>.db` olarak kopyalar ve yedeği yerine geçirir.
  Telegram: `/geriyukle <dosya>` (mutlak yol ya da `backups/` içindeki ad);
  REST: `POST /api/v1/backup/restore` gövdesi `{"path": "..."}`. Restore ancak
  **motor duruyorken** (`/durdur`) çalışır; aksi halde komut reddedilir.

## Canlı Trading ve Kill-Switch

Robot varsayılan olarak **canlı emir göndermez**. Çalışma modu `_resolve_mode()`
ile belirlenir ve Telegram `/durum`, `/api/v1/status`, `/api/v1/portfolio` ile
dashboard'da görünür:

| Mod | Koşul |
|-----|-------|
| `paper` | `PAPER_TRADING=True` (varsayılan) — emirler simüle edilir |
| `kill-switch` | `PAPER_TRADING=False` + `LIVE_TRADING_ENABLED=False` (varsayılan) |
| `testnet` | `PAPER_TRADING=False` + `LIVE_TRADING_ENABLED=True` + `BINANCE_TESTNET=True` |
| `live` | `PAPER_TRADING=False` + `BINANCE_TESTNET=False` + `LIVE_TRADING_ENABLED=True` |

- **Kill-switch**: `LIVE_TRADING_ENABLED=False` iken (paper dışında) hiçbir
  pozisyon açılış emri borsaya gitmez — `_submit_open` reddeder, `live_order_blocked`
  risk olayı loglanır. Canlıya geçiş için üç değişkenin **birlikte** ayarlanması
  gerekir.
- **Yeni giriş durdurma (`halt_entries`)**: açık pozisyonları korur, yalnızca
  yeni girişleri keser. `/giris kapali` (veya `dur`/`off`/`0`) ile durdurulur,
  `/giris acik` (veya `ac`/`on`/`1`) ile yeniden açılır; tek başına `/giris`
  mevcut durumu söyler. REST: `POST /api/v1/halt_entries` gövdesi `{"halt": true|false}`.
  Dashboard'da kırmızı **GİRİŞ KAPALI** rozeti gösterilir.
- **Minimum notional**: `MIN_NOTIONAL` (USDT, `0` = kapalı) altında kalan
  pozisyonlar açılmaz — `min_notional_blocked` risk olayı loglanır.
- Kill-switch modunda `start()` uyarı loglar ve Telegram'a `ATOS X UYARI: canli
  emirler kill-switch modunda` bildirimi gönderir; durum mesajında mod ve
  `Yeni giris: KAPALI/acik` satırı yer alır.

Örnek `.env` (canlıya geçiş — üçü birden):

```
PAPER_TRADING=False
BINANCE_TESTNET=False
LIVE_TRADING_ENABLED=True
MIN_NOTIONAL=5.0
```

## AI Yön Tahmini (Sprint 12)

- **Model**: `backend/app/ai/` TensorFlow derin öğrenme katmanı. Eğitim:
  `scripts/train_ai.py --symbols N --epochs E` (örnek: 400 sembol, 30 epoch →
  ~188k örnek, val_acc ~0.63). Çıktı `backend/app/models/ai_direction/`
  (gitignore'lı, sunucuya manuel kopyalanır).
- **Ortam uyarısı**: `tensorflow-intel==2.15.1` **tek başına** kurulu olmalı
  (tensorflow shim ile çifte kurulum `tf.keras`/`tf.random`'ı kırar). pandas
  yüklüyken TF 2.21 DLL hatası verir — 2.15.1 (numpy-1 ABI) kullanılır.
  CI'da TF yoktur; `test_ai_tracking.py` TF gerektirmez, `test_ai.py` ise
  yalnızca TF varsa çalışır.
- **Davranış**: TF yoksa/yüklenemezse `predictor=None` olur; sistem degrade
  modda çalışmaya devam eder (`use_ai_model` yok sayılır, hata loglanır).
- **Kapı**: `_ai_gate` — `use_ai_model=True` + `confidence >= ai_min_confidence`
  (varsayılan 0.55) ise AI yönü council kararıyla çelişmezse izin verir.
- **Görünürlük**: `/api/v1/signals` yanıtında `ai_direction`/`ai_confidence`; Telegram
  sinyal/pozisyon bildirimlerinde `AI: ...` satırı; dashboard Live Signals'da AI sütunu;
  `/koruma` editöründen `use_ai_model`, `ai_min_confidence`, `ai_model_path` değiştirilir.
- **Doğruluk izleme**: her BUY/SELL sinyali `predictions` tablosuna yazılır
  (bar_ts ile); 12 bar sonrası kapanış yönüne göre hit/miss çözülür (veri
  yetmezse pending kalır). Özet: `/api/v1/ai/stats` ve Telegram `/ai`.
  `executed` bayrağı yalnızca AI kapısından geçenlerde 1'dir.
- **Yeniden eğitim**: `python scripts/train_ai.py` ile modeli tazele; predictor
  işlem ömrü boyunca cache'lidir (`auto_trader._ai_predictor_cache`), bu yüzden
  yeni modeli yüklemek için sunucu restart gerekir.
- **Hızlı doğruluk ölçümü**: `python scripts/eval_ai.py [--symbols 80]` — arşivdeki
  sembollerde canlı çözümleme semantiğiyle (BUY: +12 bar yükseliş hit; SELL: düşüş
  hit; HOLD hariç) genel + yön bazlı + sembol başına son `--recent-bars` (200 ≈ 1 ay)
  accuracy hesaplar. Beklenti (06.08): genel ~0.61, BUY ~0.60, SELL ~0.62, son 1 ay
  ~0.59. Canlı `/api/v1/ai/stats` döngüsü bu değerlere yakınsamalı; acc < ~0.55 ise
  yeniden eğitim düşünülür. CI'da çalışmaz (TF yok).
- **Backtest AI simülasyonu**: `python scripts/ai_backtest.py [--symbols 60]
  [--threshold 0.55]` — strateji sinyallerine AI kapısını geriye dönük uygular
  (motor `BacktestEngine.run(..., ai_blocks=)`; `app/ai/backtest_sim.py`).
  Rapor: engellenen vs geçen sinyallerin isabet oranı + temiz vs AI filtreli
  trade/win rate/net. Ölçümler (06.08, TTP): 60 sembol/432 sinyal — AI %80'ini
  engelledi; engellenenler %38 vs geçenler %70 isabet; net +1,328 → +10,285 USDT,
  win rate %32.6 → %54.0. **200 sembol/1305 sinyal**: temiz -3,782 USDT (zarar)
  → AI filtreli +20,143 USDT, win rate %32.8 → %48.6. **TAM ARŞİV 550 sembol/
  3614 sinyal (491 başarılı)**: AI %78 engelledi; engellenen %43 vs geçen %65
  isabet; temiz 3.650 trade → AI 823 trade; win rate %35.0 → %45.1; net +25,531 →
  **+40,222 USDT** (AI filtresi neti %58 artırıyor, riski ~4,4 kat azaltıyor).
  Eşik duyarlılığı (200 sembol): 0.50 → 463 geçen, +19,793; **0.55 → 280 geçen,
  +20,143**; 0.60 → 179 geçen, +18,462, %72 isabet → **`ai_min_confidence=0.55`
  doğru denge** (0.60 daha seçici ama trade fırsatını azaltıyor). → **AI kapısı
  değer katıyor, `use_ai_model=True` doğru karar**.
- **Otomatik yeniden eğitim** (`ai_auto_retrain`): kapalıyken (`False`, varsayılan)
  yalnızca manuel `python scripts/train_ai.py` ile eğitilir + restart ile yüklenir.
  Açıkken scan döngüsü 15 dakikada bir tetikleyicileri değerlendirir: (1) zaman —
  `ai_retrain_interval_hours` geçtiyse (varsayılan 24h); (2) accuracy —
  `ai_retrain_min_samples` (30) kadar çözülmüş tahmin birikmişken `ai_retrain_min_acc`
  (0.55) altındaysa + son eğitim 6 saatten eskiyse. Eğitim `scripts/train_ai.py`'yi
  ayrı süreçte çalıştırır (event loop bloke olmaz, 30 dk timeout); başarıda predictor
  cache'i temizlenir → bir sonraki tahminde yeni model yüklenir (restart gerekmez).
  Başlangıç/başarı/hata Telegram'dan bildirilir. `/koruma` ile değiştirilebilir:
  `ai_auto_retrain`, `ai_retrain_interval_hours`, `ai_retrain_min_acc`,
  `ai_retrain_min_samples`, `ai_retrain_symbols`, `ai_retrain_epochs`.
  Durum: `/ai` komutu ve `/api/v1/ai/stats` yanıtında (`auto_retrain`,
  `last_trained_at`).

## Doğrulama

```bash
cd backend
python -m pytest tests -q   # tüm paket (risk/engel/koruma/telegram testleri dahil)
```
