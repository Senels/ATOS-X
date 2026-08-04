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

- `/pozisyon` yanıtı her pozisyon için `PnL: N (+%P)` ekler.
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
  kapanan işlem (W/L), win rate, günlük PnL, günlük net (kapanan),
  gerçekleşmemiş PnL, en iyi işlem, açık pozisyon sayısı, aktif
  durmalar (ardışık/günlük zarar, equity taban) ve son risk olayını
  içerir.

## Telegram Komutları

Bot, long-polling (`getUpdates`) ile komutları dinler. Token/chat_id
yoksa dinleyici devre dışıdır.

| Komut | Yanıt |
|---|---|
| `/durum` veya `/status` | Equity, açık pozisyon + korumalı sayısı, LONG/SHORT maruziyeti, aktif engeller, drawdown ve durma durumu |
| `/blok` | Aktif konsantrasyon engelleri (veya `yok`) |
| `/pozisyon` | Her pozisyon: sembol, taraf, qty, fiyat + `korumali`/`KORUMASIZ` |
| `/kapat <SEMBOL>` | Tek pozisyonu manuel kapatır (canlı fiyat ile) |
| `/sl <SEMBOL> <FIYAT>` | Açık pozisyonun SL'sini günceller (Telegram üstünden manuel stop) |
| `/tp <SEMBOL> <FIYAT>` | Açık pozisyonun TP'sini günceller (SL korunur) |
| `/kapatall` | Açık tüm pozisyonları canlı fiyatlarla kapatır (`close_all`) |
| `/koruma` veya `/ayar` | Risk eşiklerini gösterir; `/koruma <anahtar> <deger>` ile canlı değiştirir (kalıcı) |
| `/durdur` veya `/stop` | Acil durdurma; tüm pozisyonları kapatır + kapanış özeti gönderir |
| `/sinyal <SEMBOL>` veya `/signal <SEMBOL>` | Canlı kline'dan v23 sinyalini Telegram'a gönderir |
| `/ac` veya `/resume` | Motoru yeniden başlatır |
| `/rapor` veya `/report` | Günlük raporu anında gönderir (gerçekleşmemiş PnL, durmalar, risk olayları) |
| `/risk` | Risk durumu: equity, LONG/SHORT maruziyeti, drawdown, ardışık/günlük zarar, equity tabanı ve tüm durma durumları |
| `/gecmis [N]` | Son N kapanış işlemi (varsayılan 5, en fazla 20) |
| `/yardim` veya `/help` | Komut listesi |

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
  `breakeven_activate_pct`, `max_position_age_hours`, `max_leverage`.
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

## API ve Dashboard Görünürlüğü

| Uç | İçerik |
|---|---|
| `/health` | `protected_positions`, `concentration` |
| `/api/v1/status` | `protected_positions`, `concentration`, pozisyon sayısı |
| `/api/v1/positions` | Pozisyon başına `protected` bayrağı + `protected`/`unprotected` sayıları |
| `/api/v1/risk/positions` | Pozisyon bazlı risk: `notional`, `size_pct`, `sl_distance_pct`, `risk_amount`, `upnl`, `protected`/`trailing`/`breakeven`, `age_hours` + toplamlar (`total_notional`, `total_risk_amount`) |
| `/api/v1/priority` | Tarama listesi (`scanned`) ve rank edilmiş semboller (`symbols`) |
| `/api/v1/signals` | Tarama listesi için canlı v23 sinyalleri (`BUY`/`SELL`/`HOLD`): `symbol`, `price`, `sl`, `tp`, `reason`, `indicator`; `limit` (varsayılan 12) ve `interval` |
| `/api/v1/positions/<S>/sl` | Dashboard/API'den açık pozisyon SL güncelleme (`AutoTrader.update_sl`) |
| `/api/v1/positions/<S>/tp` | Dashboard/API'den açık pozisyon TP güncelleme (`AutoTrader.update_tp`) |
| `/api/v1/positions/<S>/close` | Açık pozisyonu canlı fiyatla kapatma (`AutoTrader.close_position`) |
| `/api/v1/market/regime` | Tek sembol rejim/volatilite/likidite tespiti (`symbol`, `interval`) |
| `/api/v1/market/regimes` | Tarama listesi için rejim özeti (`limit`, `interval`) |
| `/api/v1/market/scores` | Tarama listesi için momentum/score sıralaması (`limit`, `interval`) |
| `/api/v1/market/decision` | Tek sembol Decision Council kararı (`symbol`, `interval`) |
| `/api/v1/market/decisions` | Tarama listesi karar özeti (BUY/SELL/HOLD + confidence) |
| `/dashboard/metrics` | Aynı + pozisyon başına `protected` |
| `/dashboard` | Pozisyon tablosunda `KORUMALI`/`KORUMASIZ` rozetleri + kart özeti; `🧮 Position Risk` kartında notional, size %, SL mesafesi, risk tutarı, uPnL ve pozisyon yaşı; `📡 Live Signals` kartında tarama listesinin canlı sinyalleri (60 sn'de bir yenilenir; üstten interval seçilir — varsayılan `4h`, seçim `localStorage`'da saklanır); `🌡️ Market Regime` kartında trend/volatilite rejimi + ATR%; `🏆 Coin Scores` kartında skor sıralaması; `⚖️ Decision Council` kartında kararlar + güven + kaynaklar; her satırda SL/TP düzenleme + `Uygula`/`Kapat` butonları |

## Doğrulama

```bash
cd backend
python -m pytest tests -q   # tüm paket (risk/engel/koruma/telegram testleri dahil)
```
