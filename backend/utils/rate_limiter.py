"""
Rate limiting utilities for Spinr API.

This module provides configurable rate limiting with support for:
- IP-based limiting
- User-based limiting
- Per-endpoint limits
- Redis-backed distributed limiting (for production)
"""
import time
import hashlib
from typing import Optional, Dict, Callable, Any
from functools import wraps
from fastapi import Request, HTTPException, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from loguru import logger


# ============================================================================
# Rate Limiter Configuration
# ============================================================================

# Default limiter using IP address
default_limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute", "1000/hour"],
    storage_uri="memory://",  # Use Redis in production: "redis://localhost:6379"
)


# ============================================================================
# Custom Key Functions
# ============================================================================

def get_client_identifier(request: Request) -> str:
    """
    Get a unique client identifier combining IP and user info.
    
    Priority:
    1. User ID from auth (if authenticated)
    2. Phone number from request (for OTP endpoints)
    3. IP address (fallback)
    """
    # Try to get user ID from request state (set by auth middleware)
    if hasattr(request.state, 'user') and request.state.user:
        user_id = request.state.user.get('id')
        if user_id:
            return f"user:{user_id}"
    
    # Try to get phone from request body (for OTP requests)
    try:
        import asyncio
        # Note: This is a best-effort attempt, body may already be consumed
        # For actual phone-based limiting, apply decorator directly with phone param
    except Exception:
        pass
    
    # Fallback to IP
    return f"ip:{get_remote_address(request)}"


def get_phone_based_key(request: Request) -> str:
    """Get rate limit key based on phone number for OTP endpoints."""
    # Try to extract phone from path or query params
    phone = request.path_params.get('phone') or request.query_params.get('phone')
    if phone:
        # Hash the phone for privacy in logs
        phone_hash = hashlib.sha256(phone.encode()).hexdigest()[:16]
        return f"phone:{phone_hash}"
    
    # Fallback to IP
    return f"ip:{get_remote_address(request)}"


# ============================================================================
# Rate Limit Decorators
# ============================================================================

def rate_limit_auth(
    requests: int = 5,
    period: int = 60,
    key_func: Callable = get_client_identifier
):
    """
    Rate limit decorator for authentication endpoints.
    
    Args:
        requests: Number of allowed requests
        period: Time period in seconds
        key_func: Function to extract the rate limit key
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # The actual rate limiting is handled by SlowAPI
            # This wrapper adds logging and custom error handling
            try:
                return await func(*args, **kwargs)
            except RateLimitExceeded as e:
                logger.warning(
                    f"Rate limit exceeded for {key_func.__name__}: "
                    f"{requests} requests per {period}s"
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": f"Too many requests. Please wait {period} seconds before trying again.",
                        "retry_after": period,
                        "limit": requests,
                        "period": period
                    },
                    headers={
                        "Retry-After": str(period),
                        "X-RateLimit-Limit": str(requests),
                        "X-RateLimit-Remaining": "0"
                    }
                )
        return wrapper
    return decorator


# ============================================================================
# Pre-configured Rate Limiters for Specific Endpoints
# ============================================================================

# OTP endpoints - very restrictive to prevent abuse
otp_rate_limit = default_limiter.limit("3/minute")

# Login endpoints - moderately restrictive  
login_rate_limit = default_limiter.limit("5/minute")

# General API endpoints - more permissive
api_rate_limit = default_limiter.limit("30/minute")

# Ride creation - prevent spam ride requests
ride_request_limit = default_limiter.limit("10/minute")

# Location updates - allow frequent updates for drivers
location_update_limit = default_limiter.limit("60/minute")

# Document uploads - restrictive to prevent abuse
document_upload_limit = default_limiter.limit("5/minute")

# Admin endpoints - restrictive for security
admin_rate_limit = default_limiter.limit("100/minute")


# ============================================================================
# Rate Limit Exceeded Handler
# ============================================================================

async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Dict[str, Any]:
    """
    Custom handler for rate limit exceeded errors.
    
    Returns a structured JSON response with retry information.
    """
    # Calculate retry time from the exception
    retry_after = getattr(exc, 'retry_after', 60)
    
    logger.warning(
        f"Rate limit exceeded | "
        f"Path: {request.url.path} | "
        f"Method: {request.method} | "
        f"IP: {get_remote_address(request)} | "
        f"Retry-After: {retry_after}s"
    )
    
    return {
        "error": "rate_limit_exceeded",
        "message": "Too many requests. Please slow down and try again later.",
        "retry_after": retry_after,
        "documentation_url": "https://spinr.app/docs/rate-limits"
    }


# ============================================================================
# Sliding Window Rate Limiter (Redis-backed for production)
# ============================================================================

class RedisRateLimiter:
    """
    Redis-backed sliding window rate limiter for production use.
    
    This provides accurate rate limiting across multiple server instances.
    """
    
    def __init__(self, redis_url: str, default_limit: int = 100, window_seconds: int = 60):
        self.redis_url = redis_url
        self.default_limit = default_limit
        self.window_seconds = window_seconds
        self._redis = None
    
    async def _get_redis(self):
        """Lazy Redis connection."""
        if self._redis is None:
            try:
                import redis.asyncio as redis
                self._redis = redis.from_url(self.redis_url)
                await self._redis.ping()
                logger.info("Connected to Redis for rate limiting")
            except ImportError:
                logger.warning("Redis not available, falling back to memory-based limiting")
                self._redis = "memory"
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}, falling back to memory-based limiting")
                self._redis = "memory"
        return self._redis
    
    async def is_rate_limited(
        self,
        key: str,
        limit: int = None,
        window: int = None
    ) -> tuple[bool, int]:
        """
        Check if a key is rate limited.
        
        Args:
            key: Unique identifier for the client
            limit: Maximum requests allowed (uses default if None)
            window: Time window in seconds (uses default if None)
            
        Returns:
            Tuple of (is_limited, remaining_requests)
        """
        limit = limit or self.default_limit
        window = window or self.window_seconds
        
        redis = await self._get_redis()
        
        if redis == "memory":
            # Fallback to memory-based limiting (not recommended for production)
            return self._memory_check(key, limit, window)
        
        # Redis-based sliding window
        now = int(time.time())
        window_start = now - window
        key = f"ratelimit:{key}"
        
        pipe = redis.pipeline()
        # Remove old entries
        pipe.zremrangebyscore(key, 0, window_start)
        # Add current request
        pipe.zadd(key, {str(now): now})
        # Count requests in window
        pipe.zcard(key)
        # Set expiry
        pipe.expire(key, window)
        
        results = await pipe.execute()
        current_count = results[2]
        
        if current_count > limit:
            return True, 0
        
        return False, limit - current_count
    
    def _memory_check(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """In-memory fallback (not thread-safe, use only for development)."""
        # This is a simple implementation - in production use Redis
        if not hasattr(self, '_memory_store'):
            self._memory_store: Dict[str, list] = {}
        
        now = time.time()
        window_start = now - window
        
        # Clean old entries
        if key in self._memory_store:
            self._memory_store[key] = [
                t for t in self._memory_store[key] if t > window_start
            ]
        else:
            self._memory_store[key] = []
        
        current_count = len(self._memory_store[key])
        
        if current_count >= limit:
            return True, 0
        
        # Record this request
        self._memory_store[key].append(now)
        
        return False, limit - current_count - 1


# ============================================================================
# Integration with FastAPI
# ============================================================================

def init_rate_limiting(app):
    """
    Initialize rate limiting for a FastAPI application.
    
    Args:
        app: FastAPI application instance
    """
    from slowapi import _rate_limit_exceeded_handler
    
    # Add the limiter to app state
    app.state.limiter = default_limiter
    
    # Add exception handler
    app.add_exception_handler(
        RateLimitExceeded,
        rate_limit_exceeded_handler
    )
    
    logger.info("Rate limiting initialized")


# ============================================================================
# Usage Examples
# ============================================================================

"""
Example usage in route handlers:

from backend.utils.rate_limiter import (
    default_limiter,
    otp_rate_limit,
    login_rate_limit,
    api_rate_limit
)

@router.post("/otp/send")
@otp_rate_limit  # 3 requests per minute
async def send_otp(phone: str):
    ...

@router.post("/login")
@login_rate_limit  # 5 requests per minute
async def login(credentials: LoginCredentials):
    ...

@router.get("/users/me")
@api_rate_limit  # 30 requests per minute
async def get_current_user():
    ...

# Custom limit
@router.post("/rides")
@default_limiter.limit("10/minute")
async def create_ride(ride_data: RideData):
    ...
"""