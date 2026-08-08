# ATOS-X — Sistem Mimarisi

## Genel Bakış

ATOS-X, Binance USDⓈ-M Futures için otonom bir trading sistemidir. FastAPI tabanlı bir backend, SQLite veritabanı ve Telegram bot arayüzü üzerine kurulmuştur.

---

## Bileşen Diyagramı

```mermaid
graph TD
    subgraph Dış Sistemler
        Binance["Binance API\n(USDⓈ-M Futures)"]
        TelegramAPI["Telegram Bot API"]
    end

    subgraph Backend ["backend/app/"]
        Main["main.py\n(FastAPI + Lifespan)"]

        subgraph API ["api/"]
            BacktestAPI["backtest.py"]
            TraderAPI["trader.py"]
            PortfolioAPI["portfolio.py"]
            RiskAPI["risk.py"]
            MarketAPI["market_intel.py"]
        end

        subgraph Strategy ["strategy/"]
            Decision["decision.py\n(Karar motoru)"]
            Tradebot["tradebot_v23.py\n(İndikatörler)"]
            MultiTF["multi_tf.py\n(MTF oylama)"]
            Analytics["analytics.py\n(Risk/Getiri metrikleri)"]
            VaR["var.py\n(VaR / CVaR)"]
            Stress["stress.py\n(Stres testi)"]
            Settings["settings.py\n(Parametre yönetimi)"]
        end

        subgraph AI ["ai/"]
            Model["model.py\n(Dense / LSTM / Ensemble)"]
            Features["features.py\n(Feature engineering)"]
            Evaluate["evaluate.py\n(Doğruluk izleme)"]
        end

        subgraph Backtest ["backtest/"]
            Engine["engine.py\n(Backtest runner)"]
            MonteCarlo["monte_carlo.py\n(Bootstrap sim.)"]
        end

        subgraph Optimization ["optimization/"]
            Search["search.py\n(GridSearch)"]
            WalkForward["walk_forward.py\n(IS/OOS WF)"]
        end

        subgraph Data ["data/"]
            Loader["loader.py\n(CSV okuma)"]
            Downloader["downloader.py\n(Binance klines)"]
        end

        subgraph Core ["core/"]
            DB["database.py\n(SQLite / atos.db)"]
            AutoTrader["auto_trader.py\n(Motor)"]
        end

        Notifications["notifications/telegram.py"]
    end

    Binance -->|WebSocket fiyatlar / REST emirler| AutoTrader
    AutoTrader --> Decision
    Decision --> Tradebot
    Decision --> MultiTF
    Decision --> Model
    AutoTrader --> DB
    AutoTrader --> Notifications
    Notifications --> TelegramAPI
    TelegramAPI -->|Komutlar| Main
    Main --> API
    API --> AutoTrader
    API --> Engine
    API --> Analytics
    API --> VaR
    API --> Stress
    Engine --> MonteCarlo
    Engine --> WalkForward
    Loader --> Engine
    Loader --> MultiTF
    Model --> Features
```

---

## Veri Akışı

### Canlı Trading

```
Binance WebSocket
    → auto_trader.on_price_update()
    → decision.decide(symbol, df, settings)
        → tradebot_v23.generate_signal()
        → [MTF] multi_tf.mtf_vote()
        → [AI]  model.Predictor.predict()
    → auto_trader.open_position() / close_position()
    → database.save_trade()
    → telegram.send(bildirim)
```

### Backtest

```
POST /api/v1/backtest/run
    → backtest.engine.run(df, settings)
        → strateji döngüsü (bar-by-bar)
        → analytics.* (Sharpe, Sortino, Calmar, MaxDD)
    → [opsiyonel] monte_carlo.run_monte_carlo()
    → [opsiyonel] walk_forward.walk_forward()
    → JSON yanıt (metrikler + equity eğrisi)
```

### Karar Zinciri

```
generate_signal(df) → RAW_SIGNAL (BUY/SELL/HOLD)
    ↓
multi_tf.mtf_vote()  → ağırlıklı oy (4h=1.0, 1h=0.6, 15m=0.3)
    ↓
Predictor.predict()  → AI olasılığı (0-1)
    ↓
decision_council     → çoğunluk oylaması
    ↓
decide() → {signal, confidence, stop_loss, take_profit, mtf}
```

---

## Veritabanı Tabloları

| Tablo | Açıklama |
|-------|----------|
| `trades` | Açık ve kapalı pozisyonlar |
| `signals` | Üretilen sinyaller geçmişi |
| `backtest_results` | Backtest özet sonuçları |
| `risk_snapshots` | Periyodik VaR anlık görüntüleri |
| `performance` | Günlük equity kayıtları |
| `ai_stats` | Model doğruluk istatistikleri |

---

## Dizin Yapısı

```
ATOS-X/
├── backend/
│   ├── app/
│   │   ├── ai/           # Model, features, evaluate
│   │   ├── api/          # FastAPI router'ları
│   │   ├── backtest/     # Engine, monte_carlo
│   │   ├── core/         # DB, auto_trader
│   │   ├── data/         # CSV loader/downloader
│   │   ├── notifications/# Telegram bot
│   │   ├── optimization/ # GridSearch, walk_forward
│   │   └── strategy/     # decision, tradebot, analytics, var, stress, multi_tf
│   ├── scripts/          # CLI araçları (train_ai.py, backfill.py)
│   └── tests/            # pytest test dosyaları
├── legacy/               # CSV veri deposu + atos.db
├── docker/               # nginx.conf
├── docs/                 # DEPLOY.md, API.md, ARCHITECTURE.md
├── Dockerfile
├── docker-compose.yml
└── Makefile
```
