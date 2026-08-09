# ATOS X — Professional Dashboard Specification

## Tasarım ilkesi

Dashboard bir **operasyon komuta merkezi** olmalı; veri deposu veya geliştirici konsolu olmamalıdır. Ana ekran yalnızca karar vermeyi etkileyen bilgileri göstermelidir.

## Katmanlar

### L0 — Command / Status
- Binance Global USDⓈ-M Futures
- ONLINE / DEGRADED / OFFLINE
- PAPER / LIVE / KILL-SWITCH
- Acil Durdur

### L1 — Portfolio
Ana KPI'lar:
- Equity
- Günlük PnL
- Drawdown
- Açık pozisyon sayısı
- Net exposure
- AI confidence

### L2 — Risk
Sadece aksiyon gerektiren riskler:
- Risk halt
- Loss halt
- Daily loss halt
- Equity floor
- Entries off
- Long/short concentration

Ayrıntılı VaR, CVaR, stres, korelasyon ve limit tabloları ana dashboard'dan çıkarılır; ayrı Risk modülüne taşınır.

### L3 — Decision / AI
Karar zinciri tek satır mantığıyla görünür:

`Strategy → AI Gate → Risk Gate → Execution`

Her karar için:
- signal
- confidence
- approved / rejected
- reject reason

### L4 — Market Intelligence
Ana dashboard'da yalnızca:
- market regime
- priority watchlist
- en güçlü fırsatlar

Makro takvim, haber akışı, coin intelligence ve ayrıntılı indikatör tabloları ayrı analiz modüllerinde tutulur.

### L5 — Execution / Positions
Ana görünümde sadece aktif risk:
- symbol
- side
- entry
- current PnL
- SL/TP protection
- position age

İşlem geçmişi ve ayrıntılı trade analytics ayrı sayfada tutulur.

### L6 — Research / Factory
Dashboard'dan çıkarılan ağır ekranlar:
- Strategy Factory
- Backtest
- Optimization
- AI training
- Dataset quality
- API console

Bunlar uzman kullanıcı modülleridir ve ana operasyon ekranını kirletmez.

## Bilinçli olarak kaldırılan kalabalık

- Tekrarlanan equity kartları
- Aynı bilgiyi farklı tablolarla gösterme
- Çok sayıda ham indikatör
- API endpoint konsolu
- Tüm parametrelerin ana sayfada bulunması
- Ayrıntılı veri temizleme araçları
- Geliştirici/debug bilgileri

## Güncelleme politikası

- Status/KPI: 15 saniye polling
- WebSocket mevcutsa fiyat/pozisyon verisi gerçek zamanlı katmana taşınabilir
- Kritik risk durumu değiştiğinde polling beklenmeden UI alarmı verilmesi hedeflenir

## Güvenlik

Acil durdurma görünür ve ayrı tutulur. Canlı emir fonksiyonları dashboard görselliğiyle karıştırılmaz. Dashboard kârlılık garantisi vermez; yalnızca sistem durumunu ve karar verisini sunar.
