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

## Pozisyon Yaşı ve Yeniden Başlatma

`/ac` (veya motor yeniden başlatma) sonrası `reconcile_positions` geri
yüklenen pozisyonların gerçek açılış zamanını `trades.entry_time`
kaydından okur. Böylece time-stop (pozisyon yaşı) yeniden başlatmada
sıfırlanmaz; yaşı aşan pozisyonlar yine `time_stop` ile kapanır.
DB'de OPEN kaydı yoksa açılış zamanı şimdi kabul edilir.

## Telegram Bildirimleri

- **Başlangıç**: `_notify_startup_state` risk eşiklerini ve mevcut
  engelleri bildirir.
- **Durum değişimi**: `_sync_block_state` engel seti değişince
  eklendi/kaldırıldı özetini gönderir (değişiklik yoksa sessiz).
- **Periyodik özet**: Engeller aktifken saatte bir
  `N konsantrasyon engeli aktif: ...` hatırlatması.
- **Koruma**: Kayıp SL/TP algo emri borsada yoksa tamir edilir;
  edilemezse `ATOS X UYARI` ile manuel müdahale çağrısı yapılır.
- **Günlük rapor**: `DAILY_REPORT_HOUR`'da özet rapor gönderilir.

## Telegram Komutları

Bot, long-polling (`getUpdates`) ile komutları dinler. Token/chat_id
yoksa dinleyici devre dışıdır.

| Komut | Yanıt |
|---|---|
| `/durum` veya `/status` | Equity, açık pozisyon + korumalı sayısı, LONG/SHORT maruziyeti, aktif engeller, drawdown ve durma durumu |
| `/blok` | Aktif konsantrasyon engelleri (veya `yok`) |
| `/pozisyon` | Her pozisyon: sembol, taraf, qty, fiyat + `korumali`/`KORUMASIZ` |
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

## API ve Dashboard Görünürlüğü

| Uç | İçerik |
|---|---|
| `/health` | `protected_positions`, `concentration` |
| `/api/v1/status` | `protected_positions`, `concentration`, pozisyon sayısı |
| `/api/v1/positions` | Pozisyon başına `protected` bayrağı + `protected`/`unprotected` sayıları |
| `/dashboard/metrics` | Aynı + pozisyon başına `protected` |
| `/dashboard` | Pozisyon tablosunda `KORUMALI`/`KORUMASIZ` rozetleri + kart özeti |

## Doğrulama

```bash
cd backend
python -m pytest tests -q   # tüm paket (risk/engel/koruma/telegram testleri dahil)
```
