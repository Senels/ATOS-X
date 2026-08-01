"""
Test script validating the core Pine Script logic from ttptsl_martingale.pine.

Validates: entry rules, martingale limits, direction detection,
stop activation at 5 levels, SL/TP PERC pricing, trailing TP triggers,
break-even SL adjustment, and SL trailing with high/low prices.
"""

import math


# ── Configuration (matching Pine Script defaults) ──────────────────────────
LONG_SL_PERC = 7.5 / 100   # longTrailingStopLossPerc
SHORT_SL_PERC = 7.5 / 100  # shortTrailingStopLossPerc
LONG_TP_PERC = 10.0 / 100  # longTakeProfitPerc
SHORT_TP_PERC = 10.0 / 100 # shortTakeProfitPerc
MAX_PYRAMID = 5

# Trailing enums
class Trailing:
    tp = "TP"
    on = "ON"
    off = "OFF"


# ── Core functions (transcribed directly from .pine) ───────────────────────

def valid_open_long_position(open_long_signal: bool, position_size: float, open_trades: int) -> bool:
    """Line 115: validOpenLongPosition"""
    return open_long_signal and (position_size >= 0) and (open_trades < MAX_PYRAMID)


def valid_open_short_position(open_short_signal: bool, position_size: float, open_trades: int) -> bool:
    """Line 116: validOpenShortPosition"""
    return open_short_signal and (position_size <= 0) and (open_trades < MAX_PYRAMID)


def long_is_active(position_size: float) -> bool:
    """Line 119: longIsActive"""
    return position_size > 0


def short_is_active(position_size: float) -> bool:
    """Line 120: shortIsActive"""
    return position_size < 0


def long_stop_active(open_trades: int, position_size: float) -> bool:
    """Line 309: longStopActive"""
    return open_trades >= MAX_PYRAMID and long_is_active(position_size)


def short_stop_active(open_trades: int, position_size: float) -> bool:
    """Line 310: shortStopActive"""
    return open_trades >= MAX_PYRAMID and short_is_active(position_size)


def get_long_stop_loss_price_perc(base_src: float) -> float:
    """Line 168-171: PERC method"""
    return base_src * (1 - LONG_SL_PERC)


def get_short_stop_loss_price_perc(base_src: float) -> float:
    """Line 189-192: PERC method"""
    return base_src * (1 + SHORT_SL_PERC)


def get_long_take_profit_price_perc(close: float) -> float:
    """Line 236-238: PERC method"""
    return close * (1 + LONG_TP_PERC)


def get_short_take_profit_price_perc(close: float) -> float:
    """Line 263-265: PERC method"""
    return close * (1 - SHORT_TP_PERC)


def long_tp_trailing_executed(long_is_active_: bool, prev_executed: bool,
                              tp_price: float, high: float) -> bool:
    """Line 256: longTrailingTakeProfitExecuted assignment."""
    # not na(tp_price) and high >= tp_price
    tp_triggered = (tp_price is not None) and (high >= tp_price)
    return long_is_active_ and (prev_executed or tp_triggered)


def short_tp_trailing_executed(short_is_active_: bool, prev_executed: bool,
                               tp_price: float, low: float) -> bool:
    """Line 283: shortTrailingTakeProfitExecuted assignment."""
    tp_triggered = (tp_price is not None) and (low <= tp_price)
    return short_is_active_ and (prev_executed or tp_triggered)


def long_tp_trailing_enabled(stop_loss_trailing: str, long_tp_executed: bool) -> bool:
    """Line 175: longTakeProfitTrailingEnabled"""
    return stop_loss_trailing == Trailing.on or (stop_loss_trailing == Trailing.tp and long_tp_executed)


def short_tp_trailing_enabled(stop_loss_trailing: str, short_tp_executed: bool) -> bool:
    """Line 196: shortTakeProfitTrailingEnabled"""
    return stop_loss_trailing == Trailing.on or (stop_loss_trailing == Trailing.tp and short_tp_executed)


def compute_long_sl_price(long_is_active_: bool, valid_open_long: bool,
                          trailing_enabled: bool, high: float,
                          avg_entry_price: float, break_even: bool,
                          tp_executed: bool, prev_sl_price: float) -> float:
    """
    Lines 178-187: longStopLossPrice logic (fixed: uses avg_entry_price).
    Returns the new SL price or None (na).
    """
    if not long_is_active_:
        return None

    if valid_open_long:
        # on entry: base = close
        return get_long_stop_loss_price_perc(close=avg_entry_price)

    # else: existing position, re-evaluate
    base_src = high if trailing_enabled else avg_entry_price
    stop_price = get_long_stop_loss_price_perc(base_src)

    if break_even and tp_executed:
        stop_price = max(stop_price, avg_entry_price)

    # ratchet up: never lower than previous
    prev = prev_sl_price if prev_sl_price is not None else 0.0
    return max(stop_price, prev)


def compute_short_sl_price(short_is_active_: bool, valid_open_short: bool,
                           trailing_enabled: bool, low: float,
                           avg_entry_price: float, break_even: bool,
                           tp_executed: bool, prev_sl_price: float) -> float:
    """
    Lines 199-208: shortStopLossPrice logic (fixed: uses avg_entry_price).
    """
    if not short_is_active_:
        return None

    if valid_open_short:
        return get_short_stop_loss_price_perc(close=avg_entry_price)

    base_src = low if trailing_enabled else avg_entry_price
    stop_price = get_short_stop_loss_price_perc(base_src)

    if break_even and tp_executed:
        stop_price = min(stop_price, avg_entry_price)

    prev = prev_sl_price if prev_sl_price is not None else 999999.9
    return min(stop_price, prev)


# ── Tests ──────────────────────────────────────────────────────────────────

passed = 0
failed = 0

def check(name: str, ok: bool):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


def test_initial_long_entry():
    """No position → entry allowed."""
    result = valid_open_long_position(open_long_signal=True, position_size=0.0, open_trades=0)
    check("initial_long_entry", result is True)


def test_martingale_long_entry():
    """Already long, <5 levels → entry allowed."""
    result = valid_open_long_position(open_long_signal=True, position_size=2.0, open_trades=2)
    check("martingale_long_entry", result is True)


def test_martingale_long_blocked():
    """Already 5 levels → entry blocked."""
    result = valid_open_long_position(open_long_signal=True, position_size=5.0, open_trades=5)
    check("martingale_long_blocked", result is False)


def test_initial_short_entry():
    """No position → short entry allowed."""
    result = valid_open_short_position(open_short_signal=True, position_size=0.0, open_trades=0)
    check("initial_short_entry", result is True)


def test_short_entry_blocked_when_long():
    """Long position exists → short entry blocked."""
    result = valid_open_short_position(open_short_signal=True, position_size=3.0, open_trades=3)
    check("short_entry_blocked_when_long", result is False)


def test_stop_activation_only_at_5():
    """Stop active only when opentrades >= 5."""
    for n in range(1, 5):
        r = long_stop_active(open_trades=n, position_size=1.0)
        if r:
            check(f"stop_activation_at_{n}_levels_should_be_False", False)
            return
    r5 = long_stop_active(open_trades=5, position_size=1.0)
    check("stop_activation_at_5_levels", r5 is True)


def test_sl_price_perc_long():
    """SL price = base * (1 - perc) for long."""
    base = 100.0
    sl = get_long_stop_loss_price_perc(base)
    expected = 100.0 * (1 - LONG_SL_PERC)
    check("sl_price_perc_long", abs(sl - expected) < 1e-9)


def test_sl_price_perc_short():
    """SL price = base * (1 + perc) for short."""
    base = 100.0
    sl = get_short_stop_loss_price_perc(base)
    expected = 100.0 * (1 + SHORT_SL_PERC)
    check("sl_price_perc_short", abs(sl - expected) < 1e-9)


def test_tp_price_perc_long():
    """TP price = close * (1 + perc) for long."""
    close = 100.0
    tp = get_long_take_profit_price_perc(close)
    expected = 100.0 * (1 + LONG_TP_PERC)
    check("tp_price_perc_long", abs(tp - expected) < 1e-9)


def test_tp_price_perc_short():
    """TP price = close * (1 - perc) for short."""
    close = 100.0
    tp = get_short_take_profit_price_perc(close)
    expected = 100.0 * (1 - SHORT_TP_PERC)
    check("tp_price_perc_short", abs(tp - expected) < 1e-9)


def test_trailing_tp_trigger_long():
    """TP executed when high >= longTakeProfitPrice and not na."""
    tp_price = 110.0
    # high below -> not triggered
    r1 = long_tp_trailing_executed(True, False, tp_price, 109.0)
    check("trailing_tp_long_not_triggered_low_high", r1 is False)
    # high at or above -> triggered
    r2 = long_tp_trailing_executed(True, False, tp_price, 110.0)
    check("trailing_tp_long_triggered_at_equal", r2 is True)
    r3 = long_tp_trailing_executed(True, False, tp_price, 111.0)
    check("trailing_tp_long_triggered_above", r3 is True)
    # nil guard: tp_price is None -> not triggered
    r4 = long_tp_trailing_executed(True, False, None, 200.0)
    check("trailing_tp_long_nil_guard", r4 is False)


def test_trailing_tp_trigger_short():
    """TP executed when low <= shortTakeProfitPrice and not na."""
    tp_price = 90.0
    # low above -> not triggered
    r1 = short_tp_trailing_executed(True, False, tp_price, 91.0)
    check("trailing_tp_short_not_triggered_high_low", r1 is False)
    # low at or below -> triggered
    r2 = short_tp_trailing_executed(True, False, tp_price, 90.0)
    check("trailing_tp_short_triggered_at_equal", r2 is True)
    r3 = short_tp_trailing_executed(True, False, tp_price, 89.0)
    check("trailing_tp_short_triggered_below", r3 is True)
    # nil guard
    r4 = short_tp_trailing_executed(True, False, None, 1.0)
    check("trailing_tp_short_nil_guard", r4 is False)


def test_break_even_sl_adjustment():
    """After TP, SL moves to at least entry price (long)."""
    entry = 100.0
    # break_even=True, tp_executed=True, trailing_enabled=False
    sl = compute_long_sl_price(
        long_is_active_=True, valid_open_long=False,
        trailing_enabled=False, high=105.0,
        avg_entry_price=entry, break_even=True,
        tp_executed=True, prev_sl_price=95.0
    )
    # No trailing, base = entry (100), SL = 100 * 0.925 = 92.5
    # break_even: max(92.5, 100) = 100; then max(100, 95) = 100
    check("break_even_long_sl_adjusted_to_entry", abs(sl - 100.0) < 1e-9)


def test_sl_trailing_with_high():
    """When trailing enabled (TP mode + TP executed), SL uses high instead of entry."""
    entry = 100.0
    # trailing_enabled=True, break_even=False, tp_executed=True
    sl = compute_long_sl_price(
        long_is_active_=True, valid_open_long=False,
        trailing_enabled=True, high=120.0,
        avg_entry_price=entry, break_even=False,
        tp_executed=True, prev_sl_price=90.0
    )
    # base = high = 120, SL = 120 * 0.925 = 111.0; max(111, 90) = 111
    expected = 120.0 * (1 - LONG_SL_PERC)
    check("sl_trailing_with_high", abs(sl - expected) < 1e-9)


def test_short_sl_trailing_with_low():
    """Short SL trailing uses low instead of entry."""
    entry = 100.0
    sl = compute_short_sl_price(
        short_is_active_=True, valid_open_short=False,
        trailing_enabled=True, low=80.0,
        avg_entry_price=entry, break_even=False,
        tp_executed=True, prev_sl_price=110.0
    )
    # base = low = 80, SL = 80 * 1.075 = 86.0; min(86, 110) = 86
    expected = 80.0 * (1 + SHORT_SL_PERC)
    check("short_sl_trailing_with_low", abs(sl - expected) < 1e-9)


def test_long_tp_trailing_enabled_flag():
    """Trailing.tp only enables after TP executed; Trailing.on always."""
    # TP mode, not yet executed -> False
    r1 = long_tp_trailing_enabled(Trailing.tp, False)
    check("tp_trailing_mode_not_executed", r1 is False)
    # TP mode, executed -> True
    r2 = long_tp_trailing_enabled(Trailing.tp, True)
    check("tp_trailing_mode_executed", r2 is True)
    # ON mode -> always True
    r3 = long_tp_trailing_enabled(Trailing.on, False)
    check("on_trailing_mode_always", r3 is True)
    # OFF mode -> False
    r4 = long_tp_trailing_enabled(Trailing.off, True)
    check("off_trailing_mode", r4 is False)


# ── Runner ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("Initial long entry", test_initial_long_entry),
        ("Martingale long entry", test_martingale_long_entry),
        ("Martingale long blocked at 5", test_martingale_long_blocked),
        ("Initial short entry", test_initial_short_entry),
        ("Short entry blocked when long exists", test_short_entry_blocked_when_long),
        ("Stop activation only at 5 levels", test_stop_activation_only_at_5),
        ("SL price PERC long", test_sl_price_perc_long),
        ("SL price PERC short", test_sl_price_perc_short),
        ("TP price PERC long", test_tp_price_perc_long),
        ("TP price PERC short", test_tp_price_perc_short),
        ("Trailing TP trigger long", test_trailing_tp_trigger_long),
        ("Trailing TP trigger short", test_trailing_tp_trigger_short),
        ("Break even SL adjustment", test_break_even_sl_adjustment),
        ("SL trailing with high price (long)", test_sl_trailing_with_high),
        ("SL trailing with low price (short)", test_short_sl_trailing_with_low),
        ("Long TP trailing enabled flag", test_long_tp_trailing_enabled_flag),
    ]

    for name, fn in tests:
        print(f"[{name}]")
        fn()
        print()

    print(f"=" * 40)
    print(f"  Total: {passed + failed}  |  Passed: {passed}  |  Failed: {failed}")
    if failed:
        print("  SOME TESTS FAILED")
    else:
        print("  ALL TESTS PASSED")
    print(f"=" * 40)