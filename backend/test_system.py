import asyncio
import json
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

print("🚀 ATOS X Sistem Testi Başlatılıyor...")
print("=" * 50)

# 1. Modül testleri
print("\n📦 1. Modül Kontrolleri:")
try:
    from app.core.config import get_settings
    print("   ✅ Config modülü")
except Exception as e:
    print(f"   ❌ Config: {e}")

try:
    from app.core.database import Database
    print("   ✅ Database modülü")
except Exception as e:
    print(f"   ❌ Database: {e}")

try:
    from app.exchange.binance_client import BinanceClient
    print("   ✅ BinanceClient modülü")
except Exception as e:
    print(f"   ❌ BinanceClient: {e}")

try:
    from app.strategy.auto_trader import AutoTrader
    print("   ✅ AutoTrader modülü")
except Exception as e:
    print(f"   ❌ AutoTrader: {e}")

try:
    from app.strategy.tradebot_v23 import TradeBotV23
    print("   ✅ TradeBotV23 modülü")
except Exception as e:
    print(f"   ❌ TradeBotV23: {e}")

try:
    from app.websocket.client import BinanceWebSocket
    print("   ✅ WebSocket modülü")
except Exception as e:
    print(f"   ❌ WebSocket: {e}")

try:
    from app.notifications.telegram import TelegramNotifier
    print("   ✅ Telegram modülü")
except Exception as e:
    print(f"   ❌ Telegram: {e}")

try:
    from app.backtest.engine import BacktestEngine
    print("   ✅ BacktestEngine modülü")
except Exception as e:
    print(f"   ❌ BacktestEngine: {e}")

# 2. Veritabanı testi
print("\n🗄️ 2. Veritabanı Testi:")
try:
    db = Database()
    db.save_signal("TEST", "BUY", 50000, 0.8, "Test sinyali")
    trades = db.get_trades(5)
    print("   ✅ Veritabanı çalışıyor")
    print(f"   📊 {len(trades)} trade kaydı bulundu")
except Exception as e:
    print(f"   ❌ Veritabanı hatası: {e}")

# 3. Gösterge testleri (TradeBotV23 vektörize seti)
print("\n📊 3. Gösterge Testleri:")
try:
    import pandas as pd
    from app.strategy.tradebot_v23 import atr, macd, rsi, stochastic

    n = 60
    idx = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    base = pd.Series([50000 + i * 100 for i in range(n)], index=idx)
    df = pd.DataFrame({
        "open": base, "high": base + 50, "low": base - 50,
        "close": base, "volume": [1000.0] * n,
    })

    rsi_val = rsi(df["close"]).iloc[-1]
    macd_line, signal_line, hist = macd(df["close"])
    k, d = stochastic(df)
    a = atr(df, 14).iloc[-1]
    print(f"   ✅ RSI: {rsi_val:.2f} / MACD: {macd_line.iloc[-1]:.2f} / STOCH K: {k.iloc[-1]:.2f} / ATR: {a:.2f}")
except Exception as e:
    print(f"   ❌ Gösterge hatası: {e}")

# 4. Strateji testi
print("\n📈 4. Strateji Testi:")
try:
    from app.data import loader
    from app.strategy.tradebot_v23 import TradeBotV23
    df = loader.load_csv("BTCUSDT", "4h")
    bot = TradeBotV23()
    signal = bot.generate_signal(df)
    print(f"   ✅ Sinyal: {signal.get('signal', 'HOLD')}")
    print(f"   📝 Sebep: {signal.get('reason', 'N/A')}")
    print(f"   💰 Fiyat: {signal.get('price')}  SL: {signal.get('sl')}  TP: {signal.get('tp')}")
except Exception as e:
    print(f"   ❌ Strateji hatası: {e}")

print("\n" + "=" * 50)
print("✅ Test tamamlandı!")
print("🌐 Dashboard: http://localhost:5000/dashboard/html")
print("⚙️ Settings: http://localhost:5000/dashboard/settings")
