"""
Input validation utilities for Spinr API.

This module provides comprehensive validation functions for all user inputs
to ensure data integrity and prevent security vulnerabilities.
"""
import re
import uuid
from typing import Optional, Tuple, Any, Union
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from loguru import logger


# ============================================================================
# Phone Number Validation (E.164 Format)
# ============================================================================

def validate_phone(phone: str, raise_exception: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Validate phone number in E.164 format.
    
    E.164 format: +[country code][number] (e.g., +1234567890, +447911123456)
    
    Args:
        phone: Phone number string to validate
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, normalized_phone)
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    if not phone or not isinstance(phone, str):
        if raise_exception:
            raise HTTPException(status_code=400, detail="Phone number is required")
        return False, None
    
    # Remove common separators and whitespace
    cleaned = re.sub(r'[\s\-\.\(\)]', '', phone)
    
    # Must start with + followed by digits
    e164_pattern = re.compile(r'^\+[1-9]\d{6,14}$')
    
    if e164_pattern.match(cleaned):
        return True, cleaned
    
    # Try to normalize a bare number (add +1 for North America as fallback)
    bare_pattern = re.compile(r'^[1-9]\d{6,14}$')
    if bare_pattern.match(cleaned):
        normalized = f"+{cleaned}"
        logger.info(f"Normalized phone number {phone} to {normalized}")
        return True, normalized
    
    if raise_exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid phone number format. Please use E.164 format (e.g., +1234567890)"
        )
    
    return False, None


# ============================================================================
# Email Validation
# ============================================================================

def validate_email(email: str, raise_exception: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Validate email address format.
    
    Args:
        email: Email address string to validate
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, normalized_email)
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    if not email or not isinstance(email, str):
        if raise_exception:
            raise HTTPException(status_code=400, detail="Email address is required")
        return False, None
    
    # Basic email pattern (RFC 5322 simplified)
    email_pattern = re.compile(
        r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    )
    
    normalized = email.lower().strip()
    
    if email_pattern.match(normalized):
        # Additional checks
        if len(normalized) > 254:
            if raise_exception:
                raise HTTPException(status_code=400, detail="Email address too long")
            return False, None
        
        local, domain = normalized.rsplit('@', 1)
        if len(local) > 64:
            if raise_exception:
                raise HTTPException(status_code=400, detail="Email local part too long")
            return False, None
        
        return True, normalized
    
    if raise_exception:
        raise HTTPException(status_code=400, detail="Invalid email address format")
    
    return False, None


# ============================================================================
# GPS Coordinates Validation
# ============================================================================

def validate_coordinates(
    latitude: Union[float, int, str],
    longitude: Union[float, int, str],
    raise_exception: bool = True
) -> Tuple[bool, Optional[Tuple[float, float]]]:
    """
    Validate GPS coordinates.
    
    Valid ranges:
    - Latitude: -90 to 90
    - Longitude: -180 to 180
    
    Args:
        latitude: Latitude value
        longitude: Longitude value
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, (lat, lng))
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (ValueError, TypeError):
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Coordinates must be numeric values"
            )
        return False, None
    
    if not (-90 <= lat <= 90):
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail=f"Latitude must be between -90 and 90 (got {lat})"
            )
        return False, None
    
    if not (-180 <= lng <= 180):
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail=f"Longitude must be between -180 and 180 (got {lng})"
            )
        return False, None
    
    # Check for null island (0, 0) which is often a default/error value
    if lat == 0 and lng == 0:
        logger.warning("Null Island coordinates detected (0, 0)")
    
    return True, (lat, lng)


# ============================================================================
# Monetary Amount Validation
# ============================================================================

def validate_monetary_amount(
    amount: Union[float, int, str],
    min_value: float = 0,
    max_value: float = 1000000,
    allow_zero: bool = True,
    raise_exception: bool = True
) -> Tuple[bool, Optional[Decimal]]:
    """
    Validate monetary amount.
    
    Args:
        amount: Amount to validate
        min_value: Minimum allowed value (default: 0)
        max_value: Maximum allowed value (default: 1,000,000)
        allow_zero: Whether zero is allowed
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, Decimal amount)
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    try:
        decimal_amount = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Amount must be a valid number"
            )
        return False, None
    
    if not allow_zero and decimal_amount == 0:
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Amount cannot be zero"
            )
        return False, None
    
    if decimal_amount < Decimal(str(min_value)):
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail=f"Amount must be at least {min_value}"
            )
        return False, None
    
    if decimal_amount > Decimal(str(max_value)):
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail=f"Amount must not exceed {max_value}"
            )
        return False, None
    
    # Round to 2 decimal places
    rounded = decimal_amount.quantize(Decimal('0.01'))
    return True, rounded


# ============================================================================
# UUID Validation
# ============================================================================

def validate_uuid(
    uuid_value: str,
    raise_exception: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    Validate UUID format.
    
    Args:
        uuid_value: UUID string to validate
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, normalized_uuid)
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    if not uuid_value or not isinstance(uuid_value, str):
        if raise_exception:
            raise HTTPException(status_code=400, detail="UUID is required")
        return False, None
    
    try:
        parsed = uuid.UUID(uuid_value, version=4)
        return True, str(parsed)
    except ValueError:
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid UUID format"
            )
        return False, None


def validate_id(
    id_value: str,
    id_type: str = "ID",
    raise_exception: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    Validate generic ID format (alphanumeric with underscores/hyphens).
    
    Args:
        id_value: ID string to validate
        id_type: Human-readable type name for error messages
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, normalized_id)
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    if not id_value or not isinstance(id_value, str):
        if raise_exception:
            raise HTTPException(status_code=400, detail=f"{id_type} is required")
        return False, None
    
    # Allow alphanumeric, underscores, hyphens
    id_pattern = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
    
    if id_pattern.match(id_value):
        return True, id_value
    
    if raise_exception:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {id_type} format"
        )
    return False, None


# ============================================================================
# String Sanitization
# ============================================================================

def sanitize_string(
    value: str,
    max_length: int = 1000,
    allow_html: bool = False,
    strip_whitespace: bool = True,
    raise_exception: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    Sanitize string input to prevent XSS and other injection attacks.
    
    Args:
        value: String to sanitize
        max_length: Maximum allowed length
        allow_html: Whether to allow HTML tags (default: False - strips all HTML)
        strip_whitespace: Whether to strip leading/trailing whitespace
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, sanitized_string)
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    if value is None:
        if raise_exception:
            raise HTTPException(status_code=400, detail="String value is required")
        return False, None
    
    if not isinstance(value, str):
        value = str(value)
    
    # Strip whitespace if requested
    if strip_whitespace:
        value = value.strip()
    
    if not value:
        if raise_exception:
            raise HTTPException(status_code=400, detail="String value cannot be empty")
        return False, None
    
    if len(value) > max_length:
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail=f"String exceeds maximum length of {max_length} characters"
            )
        return False, None
    
    # Strip HTML if not allowed
    if not allow_html:
        # Simple HTML tag removal (for more robust sanitization, use bleach library)
        value = re.sub(r'<[^>]*>', '', value)
    
    # Check for suspicious patterns (basic SQL injection prevention)
    suspicious_patterns = [
        r'(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|ALTER)\b.*\b(FROM|INTO|TABLE|DATABASE)\b)',
        r'(--|\#|\/\*)',  # SQL comment markers
        r'(\b(SCRIPT|ALERT|EVAL)\b)',  # Common XSS patterns
    ]
    
    for pattern in suspicious_patterns:
        if re.search(pattern, value, re.IGNORECASE):
            logger.warning(f"Suspicious pattern detected in input: {pattern}")
            # Don't raise exception, just log for monitoring
    
    return True, value


# ============================================================================
# Date/Time Validation
# ============================================================================

def validate_datetime(
    dt_value: Union[str, datetime, int, float],
    allow_future: bool = True,
    allow_past: bool = True,
    min_date: Optional[datetime] = None,
    max_date: Optional[datetime] = None,
    raise_exception: bool = True
) -> Tuple[bool, Optional[datetime]]:
    """
    Validate datetime value.
    
    Args:
        dt_value: datetime string, datetime object, or Unix timestamp
        allow_future: Whether future dates are allowed
        allow_past: Whether past dates are allowed
        min_date: Minimum allowed date
        max_date: Maximum allowed date
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, datetime)
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    parsed_dt = None
    
    try:
        if isinstance(dt_value, datetime):
            parsed_dt = dt_value
        elif isinstance(dt_value, (int, float)):
            parsed_dt = datetime.fromtimestamp(dt_value)
        elif isinstance(dt_value, str):
            # Try ISO format first
            try:
                parsed_dt = datetime.fromisoformat(dt_value.replace('Z', '+00:00'))
            except ValueError:
                # Try other common formats
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%m/%d/%Y %H:%M:%S']:
                    try:
                        parsed_dt = datetime.strptime(dt_value, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    raise ValueError(f"Unable to parse datetime: {dt_value}")
    except (ValueError, TypeError, OSError):
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Invalid datetime format. Use ISO 8601 format."
            )
        return False, None
    
    now = datetime.utcnow()
    
    if not allow_future and parsed_dt > now:
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Future dates are not allowed"
            )
        return False, None
    
    if not allow_past and parsed_dt < now:
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Past dates are not allowed"
            )
        return False, None
    
    if min_date and parsed_dt < min_date:
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail=f"Date must be after {min_date.isoformat()}"
            )
        return False, None
    
    if max_date and parsed_dt > max_date:
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail=f"Date must be before {max_date.isoformat()}"
            )
        return False, None
    
    return True, parsed_dt


# ============================================================================
# Address Validation
# ============================================================================

def validate_address(
    address: str,
    raise_exception: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    Validate address format (basic validation).
    
    Args:
        address: Address string to validate
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, sanitized_address)
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    if not address or not isinstance(address, str):
        if raise_exception:
            raise HTTPException(status_code=400, detail="Address is required")
        return False, None
    
    # Basic validation - should contain some alphanumeric characters
    if len(address.strip()) < 5:
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Address appears too short"
            )
        return False, None
    
    # Must contain at least some letters or numbers
    if not re.search(r'[a-zA-Z0-9]', address):
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Address must contain alphanumeric characters"
            )
        return False, None
    
    return True, sanitize_string(address, max_length=500, raise_exception=False)


# ============================================================================
# Composite Validators
# ============================================================================

def validate_ride_location(
    pickup_lat: Any,
    pickup_lng: Any,
    dropoff_lat: Any,
    dropoff_lng: Any,
    raise_exception: bool = True
) -> Tuple[bool, Optional[Tuple[float, float, float, float]]]:
    """
    Validate all coordinates for a ride request.
    
    Args:
        pickup_lat: Pickup latitude
        pickup_lng: Pickup longitude
        dropoff_lat: Dropoff latitude
        dropoff_lng: Dropoff longitude
        raise_exception: If True, raise HTTPException on failure
        
    Returns:
        Tuple of (is_valid, (pickup_lat, pickup_lng, dropoff_lat, dropoff_lng))
        
    Raises:
        HTTPException: If raise_exception is True and validation fails
    """
    valid_pickup, pickup_coords = validate_coordinates(
        pickup_lat, pickup_lng, raise_exception
    )
    if not valid_pickup:
        return False, None
    
    valid_dropoff, dropoff_coords = validate_coordinates(
        dropoff_lat, dropoff_lng, raise_exception
    )
    if not valid_dropoff:
        return False, None
    
    # Check that pickup and dropoff are not the same
    if pickup_coords == dropoff_coords:
        if raise_exception:
            raise HTTPException(
                status_code=400,
                detail="Pickup and dropoff locations cannot be the same"
            )
        return False, None
    
    return True, (
        pickup_coords[0], pickup_coords[1],
        dropoff_coords[0], dropoff_coords[1]
    )


# ============================================================================
# Pydantic Integration
# ============================================================================

def pydantic_phone_validator(v: str) -> str:
    """Pydantic validator for phone numbers."""
    valid, normalized = validate_phone(v, raise_exception=False)
    if not valid:
        raise ValueError("Invalid phone number format")
    return normalized


def pydantic_email_validator(v: str) -> str:
    """Pydantic validator for email addresses."""
    valid, normalized = validate_email(v, raise_exception=False)
    if not valid:
        raise ValueError("Invalid email format")
    return normalized


def pydantic_coordinates_validator(v: Union[float, int, str]) -> float:
    """Pydantic validator for coordinates (use with field validators)."""
    try:
        return float(v)
    except (ValueError, TypeError):
        raise ValueError("Coordinate must be a number")