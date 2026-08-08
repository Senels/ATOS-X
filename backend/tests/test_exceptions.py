from datetime import datetime

from app.core.exceptions import (
    ErrorCode,
    InvalidSymbolException,
    MissingParameterException,
    NotFoundException,
    PositionNotFoundException,
    RateLimitException,
    ValidationException,
)


def test_error_code_enum_values():
    assert ErrorCode.INVALID_INPUT.value == "invalid_input"
    assert ErrorCode.UNAUTHORIZED.value == "unauthorized"
    assert ErrorCode.RATE_LIMIT_EXCEEDED.value == "rate_limit_exceeded"


def test_validation_exception_http_status():
    exc = ValidationException("bozuk veri")
    assert exc.http_status == 400
    assert exc.error_code == ErrorCode.INVALID_INPUT


def test_missing_parameter_exception_details():
    exc = MissingParameterException("symbol")
    assert exc.error_code == ErrorCode.MISSING_PARAMETER
    assert exc.details == {"parameter": "symbol"}


def test_invalid_symbol_exception():
    exc = InvalidSymbolException("BTCXX")
    assert exc.error_code == ErrorCode.INVALID_SYMBOL
    assert exc.details == {"symbol": "BTCXX"}
    assert exc.http_status == 400


def test_not_found_http_status():
    exc = PositionNotFoundException("BTCUSDT")
    assert exc.http_status == 404
    assert exc.error_code == ErrorCode.POSITION_NOT_FOUND


def test_rate_limit_exception():
    exc = RateLimitException(retry_after=30)
    assert exc.http_status == 429
    assert exc.details == {"retry_after": 30}


def test_to_dict_schema():
    exc = NotFoundException("veri yok")
    payload = exc.to_dict()
    assert payload["ok"] is False
    assert payload["error"] == "not_found"
    assert payload["message"] == "veri yok"
    assert payload["code"] == 404
    assert payload["timestamp"]
    datetime.fromisoformat(payload["timestamp"])


def test_to_dict_keeps_details():
    exc = RateLimitException(retry_after=10)
    assert exc.to_dict()["details"] == {"retry_after": 10}


def test_string_representation():
    exc = InvalidSymbolException("ETHXX")
    assert str(exc) == "Geçersiz sembol: ETHXX"
