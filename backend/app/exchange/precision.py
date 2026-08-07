from decimal import Decimal, getcontext

getcontext().prec = 28
def quantize_price(price: Decimal, tick_size: Decimal) -> Decimal:
    return (price // tick_size) * tick_size
def quantize_qty(qty: Decimal, step_size: Decimal) -> Decimal:
    return (qty // step_size) * step_size
def format_for_binance(value: Decimal) -> str:
    return format(value.normalize(), 'f')
