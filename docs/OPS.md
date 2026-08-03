# ATOS X — Operasyon ve Risk Rehberi

Bu rehber risk eşikleri, konsantrasyon engelleri, Telegram bildirimleri,
koruma (SL/TP) mekanizması ve ilgili API/dashboard görünürlüğünü belgeler.

## Risk Eşikleri

| Eşik | Varsayılan | Anlam |
|---|---|---|
| `max_position_pct` | 75 | Tek sembol projeksiyon pozisyonu (% equity) |
| `max_side_pct` | 150 | Tek yön (LONG/SHORT) toplam maruziyet (% equity) |

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
| `/durum` veya `/status` | Equity, açık pozisyon + korumalı sayısı, LONG/SHORT maruziyeti, aktif engeller |
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
