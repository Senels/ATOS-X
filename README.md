# ATOS X

> Enterprise Autonomous Trading Operating System — Binance USDⓈ-M Futures

## Prensip

- Reliability (güvenilirlik)
- Explainable Decisions (açıklanabilir kararlar)
- Event Driven Architecture
- Production First

## Durum

| Bileşen | Durum |
|---|---|
| Sürüm | 0.1.0 |
| Faz | Core Foundation |
| Trading | Disabled (testnet/manuel onay bekliyor) |
| Backtest | Aktif (gerçek 4h OHLCV + toplu tarama + optimizasyon) |
| AI | Disabled |

## Teknoloji

- Python 3.11+ · FastAPI · pandas / numpy · SQLAlchemy (sqlite) · pydantic-settings
- Binance USDⓈ-M Futures (testnet destekli), websocket canlı fiyat akışı
- Telegram bildirimleri (opsiyonel)

## Repo Yapısı

```
backend/        ATOS X
├── app/
│   ├── api/        REST endpointler (backtest, health, settings)
│   ├── backtest/   backtest motoru (engine.py)
│   ├── core/       config, sqlite database
│   ├── data/       OHLCV yükleyici (yerel CSV arşivi / Binance)
│   ├── exchange/   Binance REST + websocket
│   ├── notifications/  Telegram
│   ├── optimization/   grid search
│   ├── strategy/   sinyal motoru (tradebot_v23) + canlı otomasyon
│   ├── websocket/  canlı fiyat akışı
│   └── main.py     FastAPI entry
├── scripts/      tarama (scan_backtest) + optimizasyon (optimize)
└── tests/        pytest (31 test)
legacy/data/    backtest için yerel OHLCV CSV arşivi (futures_4h/30m/15m/2h)
docs/           mimari dokümanlar
```

## Başlatma

```bash
cp backend/.env.example backend/.env   # Binance/Telegram kimlikleri
make install
make test
make dev        # http://localhost:8000/health
```

Backtest çalıştırmak için `legacy/data/futures_4h_data/` altında
`<SYMBOL>_4h.csv` dosyaları gerekir (örn. `BTCUSDT_4h.csv`).

## Lisans

MIT
