# ATOS-X API Referansı

Tüm endpoint'ler `/api/v1/` öneki ile çalışır. `API_KEY` tanımlıysa `X-API-Key` header'ı zorunludur.

---

## Backtest

| Yöntem | Yol | Açıklama |
|--------|-----|----------|
| `POST` | `/backtest/run` | Tek sembol backtest çalıştırır |
| `POST` | `/backtest/scan` | Çoklu sembol tarama |
| `POST` | `/backtest/optimize` | GridSearch parametre optimizasyonu |
| `POST` | `/backtest/monte-carlo` | Bootstrap Monte Carlo simülasyonu |
| `POST` | `/backtest/walk-forward` | Walk-forward IS/OOS optimizasyonu |

### `/backtest/run` Örnek

```json
// Request
{ "symbol": "BTCUSDT", "interval": "4h", "initial_capital": 10000 }

// Response (ek alanlar Sprint 15 ile eklendi)
{
  "trades": 42,
  "net_pnl": 1234.5,
  "sharpe": 1.82,
  "sortino": 2.41,
  "calmar": 0.93,
  "max_drawdown": -12.3,
  "monthly_returns": { "2024-01": 4.2, "2024-02": -1.1 }
}
```

---

## Portfolio

| Yöntem | Yol | Açıklama |
|--------|-----|----------|
| `GET` | `/portfolio/stats` | Kapalı trade'lerden risk/getiri metrikleri |
| `GET` | `/portfolio/monthly` | Ay bazlı getiri tablosu |
| `GET` | `/portfolio/summary` | Genel özet (PnL, Win Rate, Metrikler) |

---

## Risk

| Yöntem | Yol | Açıklama |
|--------|-----|----------|
| `GET` | `/risk/var` | Açık pozisyonlar için anlık VaR/CVaR |
| `POST` | `/risk/stress` | Senaryo bazlı stres testi |
| `GET` | `/risk/scenarios` | Mevcut stres senaryoları listesi |
| `GET` | `/risk/summary` | Tüm risk metrikleri özeti |

### `/risk/stress` Örnek

```json
// Request
{
  "scenarios": ["covid_2020", "luna_2022"],
  "positions": [
    { "symbol": "BTCUSDT", "side": "BUY", "entry_price": 50000, "quantity": 0.1 }
  ]
}
```

---

## Trader (Canlı Motor)

| Yöntem | Yol | Açıklama |
|--------|-----|----------|
| `GET` | `/trader/status` | Motor durumu, equity, aktif pozisyonlar |
| `POST` | `/trader/start` | Motoru başlat |
| `POST` | `/trader/stop` | Acil durdur (tüm pozisyonları kapat) |
| `POST` | `/trader/close/{symbol}` | Tek pozisyon kapat |
| `PATCH` | `/trader/settings` | Strateji ayarlarını güncelle |
| `GET` | `/trader/settings` | Mevcut ayarları getir |
| `GET` | `/trader/history` | İşlem geçmişi |

---

## Market Intelligence

| Yöntem | Yol | Açıklama |
|--------|-----|----------|
| `GET` | `/market/intel` | Tüm sembollerin piyasa skoru |
| `GET` | `/market/signal/{symbol}` | Tek sembol için sinyal üretir |
| `GET` | `/market/coin-score/{symbol}` | Trend + momentum skoru |

---

## AI

| Yöntem | Yol | Açıklama |
|--------|-----|----------|
| `POST` | `/ai/train` | Modeli yeniden eğit |
| `GET` | `/ai/stats` | Tahmin doğruluk istatistikleri |
| `POST` | `/ai/predict/{symbol}` | Anlık tahmin |

---

## Sistem

| Yöntem | Yol | Açıklama |
|--------|-----|----------|
| `GET` | `/health` | Sistem sağlık kontrolü |
| `GET` | `/data/freshness` | CSV veri tazelik durumu |
| `POST` | `/data/backfill` | Eksik veriyi doldur |

---

## Swagger UI

`http://localhost:8000/docs` adresinden interaktif API dokümantasyonuna ulaşılabilir.
ReDoc için: `http://localhost:8000/redoc`
