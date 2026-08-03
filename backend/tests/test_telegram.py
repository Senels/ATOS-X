from app import main as main_mod
from app.notifications.telegram import TelegramNotifier, _process_updates


class _FakeTrader:
    def __init__(self):
        self.equity = 10000.0
        self.max_position_pct = 75.0
        self.max_side_pct = 150.0
        self.max_drawdown_pct = 20.0
        self.drawdown_pct = 0.0
        self.risk_halted = False
        self.running = True
        self._conc_blocks = {"side:LONG"}
        self.active_positions = {
            "BTCUSDT": {"side": "BUY", "entry_price": 65000.0, "quantity": 0.5,
                        "sl_order_id": "SL_1", "tp_order_id": "TP_1"},
            "ETHUSDT": {"side": "SELL", "entry_price": 3000.0, "quantity": 2.0,
                        "sl_order_id": None, "tp_order_id": None},
        }

    async def stop(self):
        self.running = False

    async def start(self):
        self.running = True


def test_process_updates_filters_and_offsets():
    calls = []

    def handler(text):
        calls.append(text)
        if text.startswith("/"):
            return "reply:" + text
        return None

    updates = [
        {"update_id": 1, "message": {"text": "/durum"}},
        {"update_id": 2, "message": {"text": "plain message"}},
        {"update_id": 3, "message": {}},
    ]
    offset, replies = _process_updates(updates, handler)
    assert offset == 4
    assert replies == ["reply:/durum"]
    assert calls == ["/durum", "plain message"]


def test_process_updates_empty():
    offset, replies = _process_updates([], lambda t: "x")
    assert offset == 0
    assert replies == []


def test_command_status():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/durum")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "Equity" in reply
    assert "Engeller" in reply
    assert "side:LONG" in reply
    assert "Drawdown" in reply


def test_command_status_shows_halt():
    fake = _FakeTrader()
    fake.risk_halted = True
    fake.drawdown_pct = 25.5
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/durum")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "AKTIF" in reply
    assert "%25.5" in reply


def test_command_blocks():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/blok")
    finally:
        main_mod.auto_trader = None
    assert reply == "ATOS X aktif engeller: side:LONG"


def test_command_blocks_empty():
    fake = _FakeTrader()
    fake._conc_blocks = set()
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/blok")
    finally:
        main_mod.auto_trader = None
    assert reply == "ATOS X aktif engeller: yok"


def test_command_positions_shows_protection():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/pozisyon")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "BTCUSDT" in reply and "korumali" in reply
    assert "ETHUSDT" in reply and "KORUMASIZ" in reply


def test_command_help():
    reply = main_mod._telegram_command("/yardim")
    assert reply is not None
    assert "/durum" in reply and "/blok" in reply


def test_command_unknown_returns_none():
    assert main_mod._telegram_command("merhaba") is None
    assert main_mod._telegram_command("/bilinmeyen") is None


def test_command_stop_schedules_stop(monkeypatch):
    fake = _FakeTrader()
    main_mod.auto_trader = fake
    monkeypatch.setattr(main_mod, "_run_later", lambda coro: True)
    try:
        reply = main_mod._telegram_command("/durdur")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "DURDURULDU" in reply


def test_command_stop_when_not_running():
    fake = _FakeTrader()
    fake.running = False
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/durdur")
    finally:
        main_mod.auto_trader = None
    assert "zaten durdurulmus" in reply


def test_command_resume_schedules_start(monkeypatch):
    fake = _FakeTrader()
    fake.running = False
    main_mod.auto_trader = fake
    monkeypatch.setattr(main_mod, "_run_later", lambda coro: True)
    try:
        reply = main_mod._telegram_command("/ac")
    finally:
        main_mod.auto_trader = None
    assert reply is not None
    assert "yeniden baslatiliyor" in reply


def test_command_resume_when_running():
    main_mod.auto_trader = _FakeTrader()
    try:
        reply = main_mod._telegram_command("/ac")
    finally:
        main_mod.auto_trader = None
    assert "zaten calisiyor" in reply


def test_command_status_shows_trading_state():
    fake = _FakeTrader()
    fake.running = False
    main_mod.auto_trader = fake
    try:
        reply = main_mod._telegram_command("/durum")
    finally:
        main_mod.auto_trader = None
    assert "DURDURULDU" in reply


def test_listener_disabled_returns_none():
    nt = TelegramNotifier.__new__(TelegramNotifier)
    nt.enabled = False
    assert nt.start_listener(lambda t: None) is None
