# ATOS X

> Enterprise Autonomous Trading Operating System — Binance USDⓈ-M Futures

## Prensip

- Reliability (güvenilirlik)
- Explainable Decisions (açıklanabilir kararlar)
- Event Driven Architecture
- AI Assisted Decision Support
- Enterprise Quality
- Production First

## Durum

| Bileşen | Durum |
|---|---|
| Sürüm | 0.1.0 |
| Faz | Sprint 1 — Core Foundation |
| Trading | Disabled (legacy: `legacy/bot/`) |
| AI | Disabled |
| Governor | Disabled |
| Risk Engine | Disabled |

## Teknoloji

- Python 3.13 (yerel: 3.11+) · FastAPI · SQLAlchemy 2 (async) · Pydantic v2
- PostgreSQL + TimescaleDB · Redis · Docker
- Frontend (ileriki sprint): React + TypeScript + TailwindCSS

## Repo Yapısı

```
backend/        ATOS X (yeni mimari)
├── app/
│   ├── core/       config (pydantic-settings), event bus
│   ├── db/         SQLAlchemy async engine/session
│   ├── exchange/   Binance async REST + WS (Sprint 3)
│   └── main.py     FastAPI entry + /health
└── tests/
frontend/       (Sprint 5)
docs/           mimari dokümanlar
docker/         Dockerfile
docker-compose.yml
legacy/         eski kodlar (korunmuş)
├── bot/        eski momentum botu (stop-order fix'li)
├── strategies/ TradingView .pine
└── research/   backtest/optimizasyon araçları
scripts/  tests/  tools/  Makefile  .env.example
```

## Roadmap

1. Core Foundation ✅ · 2. Infrastructure · 3. Binance Connector · 4. Market Collector · 5. Dashboard · 6. Market Intelligence · 7. Coin Intelligence · 8. Decision Council · 9. Governor · 10. Trading

## Başlatma

```bash
cp .env.example .env
make install
make test
make dev        # http://localhost:8000/health
# veya Docker:
make up
```

## Lisans

MIT
