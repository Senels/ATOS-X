from app.exchange.binance_client import BinanceClient


class TestPriceFormatting:
    def setup_method(self):
        self.bc = BinanceClient()
        self.bc.symbol_filters = {
            "BTCUSDT": {
                "PRICE_FILTER": {"tickSize": "0.10"},
                "LOT_SIZE": {"stepSize": "0.001"},
            },
            "1000SATSUSDT": {
                "PRICE_FILTER": {"tickSize": "0.00000001"},
                "LOT_SIZE": {"stepSize": "1"},
            },
            "ETHUSDT": {
                "PRICE_FILTER": {"tickSize": "0.010"},
                "LOT_SIZE": {"stepSize": "0.001"},
            },
        }

    def test_price_str_uses_tick_precision(self):
        # float -> bilimsel gosterim uretmesin; tickSize ondaligina yuvarliyor
        assert self.bc._price_str("BTCUSDT", 65123.456) == "65123.5"
        assert self.bc._price_str("1000SATSUSDT", 1.013e-05) == "0.00001013"
        assert self.bc._price_str("ETHUSDT", 3011.119) == "3011.12"

    def test_qty_str_uses_step_precision(self):
        assert self.bc._qty_str("BTCUSDT", 1.23456) == "1.235"
        assert self.bc._qty_str("1000SATSUSDT", 1482213.7) == "1482214"

    def test_filters_default_on_missing_symbol(self):
        assert self.bc._price_str("NOPEUSDT", 1.23456789) == "1.23456789"
