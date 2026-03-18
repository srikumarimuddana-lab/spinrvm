"""
Enhanced Error Handling Patterns for Spinr
Provides structured error handling, custom exceptions, and error middleware.
"""
from enum import Enum
from typing import Optional, Dict, Any, List, Union
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from loguru import logger
import traceback
import json
from datetime import datetime


class ErrorCode(Enum):
    """Standardized error codes for the application."""
    # Authentication errors (1000-1999)
    AUTH_REQUIRED = 1001
    AUTH_INVALID_TOKEN = 1002
    AUTH_TOKEN_EXPIRED = 1003
    AUTH_INVALID_CREDENTIALS = 1004
    AUTH_INSUFFICIENT_PERMISSIONS = 1005
    AUTH_ACCOUNT_DISABLED = 1006
    AUTH_OTP_EXPIRED = 1007
    AUTH_OTP_INVALID = 1008
    
    # Validation errors (2000-2999)
    VALIDATION_ERROR = 2001
    VALIDATION_INVALID_FORMAT = 2002
    VALIDATION_MISSING_FIELD = 2003
    VALIDATION_INVALID_RANGE = 2004
    
    # Resource errors (3000-3999)
    RESOURCE_NOT_FOUND = 3001
    RESOURCE_ALREADY_EXISTS = 3002
    RESOURCE_CONFLICT = 3003
    RESOURCE_LOCKED = 3004
    
    # Ride errors (4000-4999)
    RIDE_NOT_FOUND = 4001
    RIDE_INVALID_STATUS = 4002
    RIDE_ALREADY_CANCELLED = 4003
    RIDE_NO_DRIVERS_AVAILABLE = 4004
    RIDE_PRICE_MISMATCH = 4005
    
    # Driver errors (5000-5999)
    DRIVER_NOT_FOUND = 5001
    DRIVER_NOT_AVAILABLE = 5002
    DRIVER_OFFLINE = 5003
    DRIVER_DOCUMENTS_PENDING = 5004
    DRIVER_DOCUMENTS_REJECTED = 5005
    
    # Payment errors (6000-6999)
    PAYMENT_FAILED = 6001
    PAYMENT_METHOD_INVALID = 6002
    PAYMENT_INSUFFICIENT_FUNDS = 6003
    PAYMENT_REFUND_FAILED = 6004
    
    # System errors (9000-9999)
    INTERNAL_ERROR = 9001
    SERVICE_UNAVAILABLE = 9002
    RATE_LIMIT_EXCEEDED = 9003
    EXTERNAL_SERVICE_ERROR = 9004


class SpinrException(Exception):
    """Base exception for Spinr application."""
    
    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        status_code: int = 500,
        details: Optional[Dict[str, Any]] = None,
        should_log: bool = True
    ):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}
        self.should_log = should_log
        self.timestamp = datetime.utcnow().isoformat()
        
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for JSON response."""
        return {
            'success': False,
            'error': {
                'code': self.error_code.value,
                'message': self.message,
                'details': self.details if self.details else None,
                'timestamp': self.timestamp
            }
        }


# Authentication exceptions
class AuthenticationException(SpinrException):
    """Base authentication exception."""
    def __init__(self, message: str = 'Authentication required', **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_REQUIRED,
            status_code=401,
            **kwargs
        )


class InvalidTokenException(AuthenticationException):
    """Invalid authentication token."""
    def __init__(self, message: str = 'Invalid authentication token'):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_INVALID_TOKEN,
            status_code=401
        )


class TokenExpiredException(AuthenticationException):
    """Expired authentication token."""
    def __init__(self, message: str = 'Token has expired'):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_TOKEN_EXPIRED,
            status_code=401
        )


class InvalidCredentialsException(AuthenticationException):
    """Invalid login credentials."""
    def __init__(self, message: str = 'Invalid credentials'):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_INVALID_CREDENTIALS,
            status_code=401
        )


class InsufficientPermissionsException(AuthenticationException):
    """User lacks required permissions."""
    def __init__(self, message: str = 'Insufficient permissions'):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_INSUFFICIENT_PERMISSIONS,
            status_code=403
        )


class AccountDisabledException(AuthenticationException):
    """User account is disabled."""
    def __init__(self, message: str = 'Account is disabled'):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_ACCOUNT_DISABLED,
            status_code=403
        )


class OTPExpiredException(AuthenticationException):
    """OTP code has expired."""
    def __init__(self, message: str = 'OTP code has expired'):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_OTP_EXPIRED,
            status_code=401
        )


class InvalidOTPException(AuthenticationException):
    """Invalid OTP code."""
    def __init__(self, message: str = 'Invalid OTP code'):
        super().__init__(
            message=message,
            error_code=ErrorCode.AUTH_OTP_INVALID,
            status_code=401
        )


# Validation exceptions
class ValidationException(SpinrException):
    """Validation error."""
    def __init__(self, message: str = 'Validation error', **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            **kwargs
        )


class InvalidFormatException(ValidationException):
    """Invalid data format."""
    def __init__(self, message: str = 'Invalid format', **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.VALIDATION_INVALID_FORMAT,
            status_code=400,
            **kwargs
        )


class MissingFieldException(ValidationException):
    """Missing required field."""
    def __init__(self, field: str, message: Optional[str] = None):
        super().__init__(
            message=message or f'Missing required field: {field}',
            error_code=ErrorCode.VALIDATION_MISSING_FIELD,
            status_code=400,
            details={'field': field}
        )


class InvalidRangeException(ValidationException):
    """Value outside valid range."""
    def __init__(self, field: str, min_val: Optional[float] = None, max_val: Optional[float] = None):
        range_str = []
        if min_val is not None:
            range_str.append(f'>= {min_val}')
        if max_val is not None:
            range_str.append(f'<= {max_val}')
        
        super().__init__(
            message=f'{field} must be {" and ".join(range_str)}',
            error_code=ErrorCode.VALIDATION_INVALID_RANGE,
            status_code=400,
            details={'field': field, 'min': min_val, 'max': max_val}
        )


# Resource exceptions
class ResourceNotFoundException(SpinrException):
    """Requested resource not found."""
    def __init__(self, resource_type: str, resource_id: str):
        super().__init__(
            message=f'{resource_type} not found: {resource_id}',
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
            status_code=404,
            details={'resource_type': resource_type, 'resource_id': resource_id}
        )


class ResourceAlreadyExistsException(SpinrException):
    """Resource already exists."""
    def __init__(self, resource_type: str, field: str, value: str):
        super().__init__(
            message=f'{resource_type} already exists with {field}: {value}',
            error_code=ErrorCode.RESOURCE_ALREADY_EXISTS,
            status_code=409,
            details={'resource_type': resource_type, 'field': field, 'value': value}
        )


class ResourceConflictException(SpinrException):
    """Resource conflict."""
    def __init__(self, message: str = 'Resource conflict', **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.RESOURCE_CONFLICT,
            status_code=409,
            **kwargs
        )


# Ride exceptions
class RideNotFoundException(ResourceNotFoundException):
    """Ride not found."""
    def __init__(self, ride_id: str):
        super().__init__('Ride', ride_id)


class RideInvalidStatusException(SpinrException):
    """Invalid ride status transition."""
    def __init__(self, current_status: str, requested_status: str):
        super().__init__(
            message=f'Cannot transition from {current_status} to {requested_status}',
            error_code=ErrorCode.RIDE_INVALID_STATUS,
            status_code=400,
            details={'current_status': current_status, 'requested_status': requested_status}
        )


class RideNoDriversAvailableException(SpinrException):
    """No drivers available for ride."""
    def __init__(self, location: Optional[Dict[str, float]] = None):
        super().__init__(
            message='No drivers available in your area',
            error_code=ErrorCode.RIDE_NO_DRIVERS_AVAILABLE,
            status_code=404,
            details={'location': location}
        )


# Driver exceptions
class DriverNotFoundException(ResourceNotFoundException):
    """Driver not found."""
    def __init__(self, driver_id: str):
        super().__init__('Driver', driver_id)


class DriverNotAvailableException(SpinrException):
    """Driver not available."""
    def __init__(self, driver_id: str):
        super().__init__(
            message='Driver is not available',
            error_code=ErrorCode.DRIVER_NOT_AVAILABLE,
            status_code=400,
            details={'driver_id': driver_id}
        )


class DriverOfflineException(SpinrException):
    """Driver is offline."""
    def __init__(self, driver_id: str):
        super().__init__(
            message='Driver is currently offline',
            error_code=ErrorCode.DRIVER_OFFLINE,
            status_code=400,
            details={'driver_id': driver_id}
        )


# Payment exceptions
class PaymentException(SpinrException):
    """Payment processing error."""
    def __init__(self, message: str = 'Payment failed', **kwargs):
        super().__init__(
            message=message,
            error_code=ErrorCode.PAYMENT_FAILED,
            status_code=400,
            **kwargs
        )


class PaymentMethodInvalidException(PaymentException):
    """Invalid payment method."""
    def __init__(self, message: str = 'Invalid payment method'):
        super().__init__(message=message)


class InsufficientFundsException(PaymentException):
    """Insufficient funds."""
    def __init__(self, required: float, available: float):
        super().__init__(
            message=f'Insufficient funds. Required: ${required:.2f}, Available: ${available:.2f}',
            details={'required': required, 'available': available}
        )


# System exceptions
class InternalErrorException(SpinrException):
    """Internal server error."""
    def __init__(self, message: str = 'Internal server error'):
        super().__init__(
            message=message,
            error_code=ErrorCode.INTERNAL_ERROR,
            status_code=500
        )


class ServiceUnavailableException(SpinrException):
    """Service temporarily unavailable."""
    def __init__(self, service_name: str = ''):
        message = f'Service temporarily unavailable'
        if service_name:
            message += f': {service_name}'
        super().__init__(
            message=message,
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503
        )


class RateLimitExceededException(SpinrException):
    """Rate limit exceeded."""
    def __init__(self, limit: int, retry_after: int):
        super().__init__(
            message='Rate limit exceeded',
            error_code=ErrorCode.RATE_LIMIT_EXCEEDED,
            status_code=429,
            details={'limit': limit, 'retry_after': retry_after}
        )


class ExternalServiceException(SpinrException):
    """External service error."""
    def __init__(self, service_name: str, message: str):
        super().__init__(
            message=f'{service_name} error: {message}',
            error_code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            status_code=502,
            details={'service': service_name}
        )


# Error handling middleware
async def spinr_exception_handler(request: Request, exc: SpinrException) -> JSONResponse:
    """Handle SpinrException and return formatted JSON response."""
    if exc.should_log:
        logger.warning(
            f'SpinrException: {exc.error_code.name} - {exc.message}',
            extra={
                'path': request.url.path,
                'method': request.method,
                'error_code': exc.error_code.value
            }
        )
    
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict()
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError
) -> JSONResponse:
    """Handle validation errors and return formatted response."""
    errors = []
    for error in exc.errors():
        errors.append({
            'field': '.'.join(str(x) for x in error.get('loc', [])),
            'message': error.get('msg', 'Invalid value'),
            'type': error.get('type', 'value_error')
        })
    
    logger.warning(
        f'Validation error: {errors}',
        extra={'path': request.url.path, 'method': request.method}
    )
    
    return JSONResponse(
        status_code=422,
        content={
            'success': False,
            'error': {
                'code': ErrorCode.VALIDATION_ERROR.value,
                'message': 'Validation error',
                'details': {'errors': errors},
                'timestamp': datetime.utcnow().isoformat()
            }
        }
    )


async def http_exception_handler(
    request: Request,
    exc: HTTPException
) -> JSONResponse:
    """Handle HTTPException and return formatted response."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            'success': False,
            'error': {
                'code': exc.status_code,
                'message': exc.detail,
                'timestamp': datetime.utcnow().isoformat()
            }
        }
    )


async def general_exception_handler(
    request: Request,
    exc: Exception
) -> JSONResponse:
    """Handle unexpected exceptions and return formatted response."""
    # Log full traceback for debugging
    logger.error(
        f'Unhandled exception: {str(exc)}\n{traceback.format_exc()}',
        extra={
            'path': request.url.path,
            'method': request.method,
            'user_agent': request.headers.get('user-agent')
        }
    )
    
    return JSONResponse(
        status_code=500,
        content={
            'success': False,
            'error': {
                'code': ErrorCode.INTERNAL_ERROR.value,
                'message': 'An unexpected error occurred',
                'timestamp': datetime.utcnow().isoformat()
            }
        }
    )


def register_exception_handlers(app):
    """Register all exception handlers with the FastAPI app."""
    app.add_exception_handler(SpinrException, spinr_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)


# Error response helpers
def error_response(
    message: str,
    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
    status_code: int = 500,
    details: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Create a standardized error response dictionary."""
    return {
        'success': False,
        'error': {
            'code': error_code.value,
            'message': message,
            'details': details,
            'timestamp': datetime.utcnow().isoformat()
        }
    }


def success_response(data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a standardized success response dictionary."""
    response = {'success': True}
    if data is not None:
        response['data'] = data
    return response