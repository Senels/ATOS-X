"""
Merkezi Exception Tanımları ve Standart Error Response Schema
"""
from enum import Enum
from typing import Any, Dict, Optional

from app.core.time import utc_now


class ErrorCode(str, Enum):
    """Standart error kodları"""
    # Validation (400)
    INVALID_INPUT = "invalid_input"
    MISSING_PARAMETER = "missing_parameter"
    INVALID_SYMBOL = "invalid_symbol"
    INVALID_RANGE = "invalid_range"
    
    # Authentication (401)
    UNAUTHORIZED = "unauthorized"
    INVALID_API_KEY = "invalid_api_key"
    
    # Permission (403)
    FORBIDDEN = "forbidden"
    INSUFFICIENT_PERMISSIONS = "insufficient_permissions"
    
    # Not Found (404)
    NOT_FOUND = "not_found"
    POSITION_NOT_FOUND = "position_not_found"
    SYMBOL_NOT_FOUND = "symbol_not_found"
    DATA_NOT_FOUND = "data_not_found"
    
    # Conflict (409)
    STATE_CONFLICT = "state_conflict"
    POSITION_ALREADY_EXISTS = "position_already_exists"
    DUPLICATE_ORDER = "duplicate_order"
    
    # Rate Limit (429)
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    
    # Server (500)
    INTERNAL_ERROR = "internal_error"
    DATABASE_ERROR = "database_error"
    EXCHANGE_ERROR = "exchange_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TIMEOUT = "timeout"


class ATOSException(Exception):
    """ATOS X ana exception base class"""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        http_status: int = 500,
        details: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.error_code = error_code
        self.http_status = http_status
        self.details = details or {}
        self.context = context or {}
        self.timestamp = utc_now().isoformat()
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """Exception'ı JSON-serializable dict'e çevir"""
        return {
            "ok": False,
            "error": self.error_code.value,
            "message": self.message,
            "code": self.http_status,
            "timestamp": self.timestamp,
            "details": self.details if self.details else None,
        }


# ===== VALIDATION EXCEPTIONS (400) =====
class ValidationException(ATOSException):
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.INVALID_INPUT,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            http_status=400,
            details=details,
        )


class MissingParameterException(ValidationException):
    def __init__(self, param_name: str):
        super().__init__(
            message=f"Gerekli parametre eksik: {param_name}",
            error_code=ErrorCode.MISSING_PARAMETER,
            details={"parameter": param_name},
        )


class InvalidSymbolException(ValidationException):
    def __init__(self, symbol: str):
        super().__init__(
            message=f"Geçersiz sembol: {symbol}",
            error_code=ErrorCode.INVALID_SYMBOL,
            details={"symbol": symbol},
        )


class InvalidRangeException(ValidationException):
    def __init__(self, param_name: str, min_val: Any, max_val: Any):
        super().__init__(
            message=f"{param_name} {min_val} ile {max_val} arasında olmalı",
            error_code=ErrorCode.INVALID_RANGE,
            details={"parameter": param_name, "min": min_val, "max": max_val},
        )


# ===== AUTHENTICATION EXCEPTIONS (401) =====
class AuthenticationException(ATOSException):
    def __init__(
        self,
        message: str = "Kimlik doğrulama başarısız",
        error_code: ErrorCode = ErrorCode.UNAUTHORIZED,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            http_status=401,
        )


class InvalidApiKeyException(AuthenticationException):
    def __init__(self):
        super().__init__(
            message="Geçersiz API key",
            error_code=ErrorCode.INVALID_API_KEY,
        )


# ===== PERMISSION EXCEPTIONS (403) =====
class PermissionException(ATOSException):
    def __init__(
        self,
        message: str = "Bu işlem için izniniz yok",
        error_code: ErrorCode = ErrorCode.FORBIDDEN,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            http_status=403,
        )


# ===== NOT FOUND EXCEPTIONS (404) =====
class NotFoundException(ATOSException):
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.NOT_FOUND,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            http_status=404,
            details=details,
        )


class PositionNotFoundException(NotFoundException):
    def __init__(self, symbol: str):
        super().__init__(
            message=f"{symbol} sembolü için açık pozisyon bulunamadı",
            error_code=ErrorCode.POSITION_NOT_FOUND,
            details={"symbol": symbol},
        )


class DataNotFoundException(NotFoundException):
    def __init__(self, data_type: str, identifier: str):
        super().__init__(
            message=f"{data_type} verisi bulunamadı: {identifier}",
            error_code=ErrorCode.DATA_NOT_FOUND,
            details={"data_type": data_type, "identifier": identifier},
        )


# ===== STATE CONFLICT EXCEPTIONS (409) =====
class ConflictException(ATOSException):
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.STATE_CONFLICT,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            http_status=409,
            details=details,
        )


class PositionAlreadyExistsException(ConflictException):
    def __init__(self, symbol: str):
        super().__init__(
            message=f"{symbol} için zaten açık pozisyon mevcut",
            error_code=ErrorCode.POSITION_ALREADY_EXISTS,
            details={"symbol": symbol},
        )


# ===== RATE LIMIT EXCEPTIONS (429) =====
class RateLimitException(ATOSException):
    def __init__(self, retry_after: int = 60):
        super().__init__(
            message=f"Rate limit aşıldı. {retry_after} saniye sonra tekrar deneyin",
            error_code=ErrorCode.RATE_LIMIT_EXCEEDED,
            http_status=429,
            details={"retry_after": retry_after},
        )


# ===== SERVER EXCEPTIONS (500) =====
class ServerException(ATOSException):
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            http_status=500,
            details=details,
        )


class DatabaseException(ServerException):
    def __init__(self, message: str = "Veritabanı hatası"):
        super().__init__(
            message=message,
            error_code=ErrorCode.DATABASE_ERROR,
        )


class ExchangeException(ServerException):
    def __init__(self, exchange: str, message: str):
        super().__init__(
            message=f"{exchange} bağlantı hatası: {message}",
            error_code=ErrorCode.EXCHANGE_ERROR,
            details={"exchange": exchange},
        )


class BinanceException(ExchangeException):
    def __init__(self, message: str):
        super().__init__("Binance", message)


class ServiceUnavailableException(ServerException):
    def __init__(self, service: str = "Service"):
        super().__init__(
            message=f"{service} geçici olarak kullanılamıyor",
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            http_status=503,
        )


class TimeoutException(ServerException):
    def __init__(self, operation: str, timeout_seconds: int):
        super().__init__(
            message=f"{operation} işlemi timeout oldu ({timeout_seconds}s)",
            error_code=ErrorCode.TIMEOUT,
            details={"operation": operation, "timeout_seconds": timeout_seconds},
        )


# ===== BUSINESS LOGIC EXCEPTIONS =====
class InsufficientEquityException(ServerException):
    def __init__(self, required: float, available: float):
        super().__init__(
            message=f"Yetersiz bakiye: {available} gerekli {required}",
            details={"required": required, "available": available},
        )


class RiskLimitException(ServerException):
    def __init__(self, limit_name: str, limit_value: float, current: float):
        super().__init__(
            message=f"{limit_name} limiti aşıldı: {current} > {limit_value}",
            details={"limit": limit_name, "limit_value": limit_value, "current": current},
        )


class DrawdownHaltException(ServerException):
    def __init__(self, current_drawdown: float, limit: float):
        super().__init__(
            message=f"Drawdown limiti aşıldı: %{current_drawdown} > %{limit}",
            details={"current_drawdown": current_drawdown, "limit": limit},
        )


class ConsecutiveLossException(ServerException):
    def __init__(self, current_losses: int, limit: int):
        super().__init__(
            message=f"Ardışık zarar limiti aşıldı: {current_losses} >= {limit}",
            details={"current_losses": current_losses, "limit": limit},
        )
