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
| Sürüm | 1.0.0 |
| Faz | Tüm sprintler tamamlandı (11/11) |
| Trading | Motor tamamlandı (varsayılan: paper + kill-switch; canlı için açık onay gerekir) |
| Backtest | Aktif (gerçek 4h OHLCV + toplu tarama + optimizasyon + TTPTSL motoru) |
| AI | Aktif (TensorFlow derin öğrenme yön tahmini, `backend/app/ai/`) |

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
│   ├── strategy/   sinyal motoru (tradebot_v23 + TTPTSL) + canlı otomasyon
│   ├── websocket/  canlı fiyat akışı
│   └── main.py     FastAPI entry
├── scripts/      tarama (scan_backtest) + optimizasyon (optimize)
└── tests/        pytest (597 test)
legacy/data/    backtest için yerel OHLCV CSV arşivi (futures_4h/30m/15m/2h)
docs/           mimari dokümanlar (ROADMAP, OPS — operasyon/risk rehberi)
```

Operasyon ve risk özellikleri (konsantrasyon engelleri, Telegram
komutları, koruma/SL-TP, canlı trading kill-switch/giriş durdurma,
DB yedekleme/geri yükleme, Telegram chat whitelist) için
bkz. [docs/OPS.md](docs/OPS.md).

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
