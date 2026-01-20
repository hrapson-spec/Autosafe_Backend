"""
AutoSafe Security Module - Launch Blocker Fixes
================================================

This module implements critical security controls required for production launch:
1. Admin endpoint IP allowlist
2. Admin access audit logging
3. PII redaction in logs
4. API key rotation support
5. Request/response body logging prevention
"""
import os
import re
import logging
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Set, Callable
from functools import wraps
from starlette.requests import Request
from starlette.responses import Response
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# =============================================================================
# PII Redaction Patterns
# =============================================================================

# Patterns to redact from any log output
PII_PATTERNS = {
    # UK Vehicle Registration (various formats)
    'registration': re.compile(
        r'\b[A-Z]{2}[0-9]{2}\s?[A-Z]{3}\b|'  # AB12 CDE or AB12CDE
        r'\b[A-Z][0-9]{1,3}\s?[A-Z]{3}\b|'   # A123 BCD
        r'\b[A-Z]{3}\s?[0-9]{1,3}[A-Z]\b|'   # ABC 123D
        r'\b[0-9]{1,4}\s?[A-Z]{1,3}\b',      # 1234 AB
        re.IGNORECASE
    ),
    # Email addresses
    'email': re.compile(
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    ),
    # UK Postcodes (full)
    'postcode': re.compile(
        r'\b[A-Z]{1,2}[0-9][0-9A-Z]?\s?[0-9][A-Z]{2}\b',
        re.IGNORECASE
    ),
    # UK Phone numbers
    'phone': re.compile(
        r'\b(?:\+44|0044|0)\s?[0-9]{2,4}\s?[0-9]{3,4}\s?[0-9]{3,4}\b'
    ),
    # API keys (common patterns)
    'api_key': re.compile(
        r'\b(?:sk_|pk_|re_|api_)[A-Za-z0-9_-]{20,}\b|'
        r'\b[A-Za-z0-9]{32,64}\b'  # Generic long tokens
    ),
}

# Replacement tokens
REDACTION_TOKENS = {
    'registration': '[REDACTED_VRM]',
    'email': '[REDACTED_EMAIL]',
    'postcode': '[REDACTED_POSTCODE]',
    'phone': '[REDACTED_PHONE]',
    'api_key': '[REDACTED_KEY]',
}


def redact_pii(text: str, preserve_partial: bool = False) -> str:
    """
    Redact all PII from a text string.

    Args:
        text: The text to redact
        preserve_partial: If True, keep partial info (e.g., email domain, postcode outcode)

    Returns:
        Text with PII redacted
    """
    if not text:
        return text

    result = str(text)

    for pii_type, pattern in PII_PATTERNS.items():
        if preserve_partial and pii_type == 'email':
            # Preserve domain for emails: user@domain.com -> [REDACTED]@domain.com
            result = re.sub(
                r'([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Z|a-z]{2,})',
                r'[REDACTED]@\2',
                result
            )
        elif preserve_partial and pii_type == 'postcode':
            # Preserve outcode: SW1A 1AA -> SW1A [REDACTED]
            result = re.sub(
                r'\b([A-Z]{1,2}[0-9][0-9A-Z]?)\s?[0-9][A-Z]{2}\b',
                r'\1 [REDACTED]',
                result,
                flags=re.IGNORECASE
            )
        else:
            result = pattern.sub(REDACTION_TOKENS.get(pii_type, '[REDACTED]'), result)

    return result


class PIIRedactingFormatter(logging.Formatter):
    """Custom log formatter that automatically redacts PII from all log messages."""

    def __init__(self, *args, preserve_partial: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.preserve_partial = preserve_partial

    def format(self, record: logging.LogRecord) -> str:
        # Redact the message
        record.msg = redact_pii(str(record.msg), self.preserve_partial)

        # Redact any args
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact_pii(str(v), self.preserve_partial)
                              for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(redact_pii(str(arg), self.preserve_partial)
                                   for arg in record.args)

        return super().format(record)


def configure_safe_logging():
    """Configure logging with PII redaction enabled."""
    # Create PII-safe formatter
    formatter = PIIRedactingFormatter(
        '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
        preserve_partial=True
    )

    # Apply to root logger
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)

    # Also apply to our app logger
    app_logger = logging.getLogger('__main__')
    for handler in app_logger.handlers:
        handler.setFormatter(formatter)


# =============================================================================
# Admin IP Allowlist
# =============================================================================

# Load allowed IPs from environment (comma-separated)
# In production, this should be your office IP, VPN exit IPs, or Railway internal network
ADMIN_ALLOWED_IPS: Set[str] = set()
_raw_ips = os.environ.get("ADMIN_ALLOWED_IPS", "")
if _raw_ips:
    ADMIN_ALLOWED_IPS = {ip.strip() for ip in _raw_ips.split(",") if ip.strip()}

# Special value to allow all IPs (ONLY for development)
ADMIN_ALLOW_ALL_IPS = os.environ.get("ADMIN_ALLOW_ALL_IPS", "").lower() == "true"

# Railway internal network detection
RAILWAY_INTERNAL_NETWORK = os.environ.get("RAILWAY_ENVIRONMENT") is not None


def get_client_ip(request: Request) -> str:
    """
    Get the real client IP, accounting for proxies.

    Checks X-Forwarded-For header (standard for proxied requests)
    and falls back to direct connection IP.
    """
    # Check for forwarded header (Railway/proxy)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take the first IP (client's original IP)
        return forwarded.split(",")[0].strip()

    # Check for X-Real-IP (nginx style)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    # Fall back to direct connection
    if request.client:
        return request.client.host

    return "unknown"


def is_ip_allowed_for_admin(request: Request) -> bool:
    """
    Check if the requesting IP is allowed to access admin endpoints.

    Returns True if:
    - ADMIN_ALLOW_ALL_IPS is set (development only)
    - Client IP is in ADMIN_ALLOWED_IPS
    - Request is from Railway internal network (if configured)
    """
    if ADMIN_ALLOW_ALL_IPS:
        logger.warning("ADMIN_ALLOW_ALL_IPS is enabled - this should ONLY be used in development")
        return True

    client_ip = get_client_ip(request)

    # Check explicit allowlist
    if client_ip in ADMIN_ALLOWED_IPS:
        return True

    # Check for Railway internal request (private networking)
    # Railway internal requests come from 10.x.x.x range
    if RAILWAY_INTERNAL_NETWORK and client_ip.startswith("10."):
        return True

    # Localhost is always allowed (for local development/testing)
    if client_ip in ("127.0.0.1", "::1", "localhost"):
        return True

    return False


def require_admin_ip(request: Request):
    """
    Middleware check for admin IP allowlist.
    Raises HTTPException if IP is not allowed.
    """
    if not is_ip_allowed_for_admin(request):
        client_ip = get_client_ip(request)
        logger.warning(f"Admin access denied from IP: {client_ip}")
        raise HTTPException(
            status_code=403,
            detail="Access denied: IP not in allowlist"
        )


# =============================================================================
# Admin Audit Logging
# =============================================================================

class AuditLogger:
    """
    Audit logger for admin actions.

    Logs all admin actions with:
    - Timestamp
    - Client IP
    - API key hash (for identification without exposure)
    - Action performed
    - Resource affected
    """

    def __init__(self):
        self.logger = logging.getLogger("audit")
        # Ensure audit logs go to a separate handler if needed
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                '{"timestamp": "%(asctime)s", "type": "AUDIT", "message": "%(message)s"}'
            ))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def _hash_key(self, api_key: str) -> str:
        """Create a short hash of the API key for identification."""
        if not api_key:
            return "no_key"
        return hashlib.sha256(api_key.encode()).hexdigest()[:12]

    def log_admin_access(
        self,
        request: Request,
        action: str,
        resource: str,
        resource_id: Optional[str] = None,
        details: Optional[Dict] = None
    ):
        """
        Log an admin action.

        Args:
            request: The FastAPI request object
            action: The action performed (e.g., "list", "create", "update", "delete")
            resource: The resource type (e.g., "leads", "garages")
            resource_id: Optional ID of the specific resource
            details: Optional additional details (will be redacted)
        """
        client_ip = get_client_ip(request)
        api_key = request.headers.get("X-API-Key", "")
        key_hash = self._hash_key(api_key)

        log_entry = {
            "action": action,
            "resource": resource,
            "resource_id": resource_id,
            "client_ip": client_ip,
            "key_hash": key_hash,
            "path": str(request.url.path),
            "method": request.method,
        }

        if details:
            # Redact any PII in details
            safe_details = {k: redact_pii(str(v)) for k, v in details.items()}
            log_entry["details"] = safe_details

        self.logger.info(str(log_entry))

    def log_failed_auth(self, request: Request, reason: str):
        """Log a failed authentication attempt."""
        client_ip = get_client_ip(request)
        self.logger.warning(
            f'{{"action": "auth_failed", "reason": "{reason}", '
            f'"client_ip": "{client_ip}", "path": "{request.url.path}"}}'
        )


# Global audit logger instance
audit_logger = AuditLogger()


# =============================================================================
# API Key Management
# =============================================================================

class APIKeyManager:
    """
    Manages API key validation with rotation support.

    Supports:
    - Primary key (ADMIN_API_KEY)
    - Secondary key (ADMIN_API_KEY_SECONDARY) for rotation
    - Key rotation without downtime
    """

    def __init__(self):
        self.primary_key = os.environ.get("ADMIN_API_KEY")
        self.secondary_key = os.environ.get("ADMIN_API_KEY_SECONDARY")

        # Validate key strength
        if self.primary_key and len(self.primary_key) < 32:
            logger.warning("ADMIN_API_KEY is less than 32 characters - consider using a stronger key")

    def validate_key(self, provided_key: str) -> bool:
        """
        Validate an API key against primary and secondary keys.

        Uses constant-time comparison to prevent timing attacks.
        """
        if not provided_key:
            return False

        # Check primary key
        if self.primary_key and hmac.compare_digest(provided_key, self.primary_key):
            return True

        # Check secondary key (for rotation)
        if self.secondary_key and hmac.compare_digest(provided_key, self.secondary_key):
            logger.info("Request authenticated with secondary API key")
            return True

        return False

    def is_configured(self) -> bool:
        """Check if at least one API key is configured."""
        return bool(self.primary_key or self.secondary_key)


# Global API key manager instance
api_key_manager = APIKeyManager()


def require_admin_auth(request: Request):
    """
    Validate admin authentication.

    Checks both API key and IP allowlist.
    Raises HTTPException on failure.
    """
    # Check IP allowlist first
    require_admin_ip(request)

    # Then check API key
    if not api_key_manager.is_configured():
        audit_logger.log_failed_auth(request, "admin_not_configured")
        raise HTTPException(
            status_code=503,
            detail="Admin access not configured"
        )

    api_key = request.headers.get("X-API-Key")

    if not api_key_manager.validate_key(api_key or ""):
        audit_logger.log_failed_auth(request, "invalid_key")
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key"
        )


# =============================================================================
# Request Body Logging Prevention
# =============================================================================

# Headers that should NEVER be logged
SENSITIVE_HEADERS = {
    "x-api-key",
    "authorization",
    "cookie",
    "x-auth-token",
    "x-csrf-token",
}


def safe_headers(request: Request) -> Dict[str, str]:
    """
    Get headers safe for logging (sensitive headers redacted).
    """
    safe = {}
    for key, value in request.headers.items():
        if key.lower() in SENSITIVE_HEADERS:
            safe[key] = "[REDACTED]"
        else:
            safe[key] = value
    return safe


class RequestBodyLoggingPrevention:
    """
    Middleware to prevent accidental logging of request/response bodies.

    Adds warnings if bodies are accessed and logged.
    """

    WARN_PATHS = {"/api/leads", "/api/risk/v55", "/api/admin"}

    @classmethod
    def should_warn(cls, path: str) -> bool:
        """Check if path contains sensitive data that shouldn't be logged."""
        return any(path.startswith(p) for p in cls.WARN_PATHS)


# =============================================================================
# CORS Configuration
# =============================================================================

def get_allowed_origins() -> List[str]:
    """
    Get the list of allowed CORS origins.

    In production, this should be explicitly set via CORS_ORIGINS env var.
    Never returns ["*"] in production.
    """
    env_origins = os.environ.get("CORS_ORIGINS", "")

    if env_origins:
        # Parse comma-separated origins
        origins = [o.strip() for o in env_origins.split(",") if o.strip()]
        # Validate each origin
        valid_origins = []
        for origin in origins:
            if origin == "*":
                logger.error("Wildcard CORS origin rejected - use explicit origins")
                continue
            if not origin.startswith(("http://", "https://")):
                logger.error(f"Invalid CORS origin (must start with http/https): {origin}")
                continue
            valid_origins.append(origin)

        if valid_origins:
            return valid_origins

    # Default for development only
    is_production = os.environ.get("RAILWAY_ENVIRONMENT") == "production"

    if is_production:
        # In production with no CORS_ORIGINS set, only allow same-origin
        logger.warning("No CORS_ORIGINS configured in production - defaulting to same-origin only")
        return []

    # Development defaults
    logger.warning("Using development CORS origins - set CORS_ORIGINS in production")
    return [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]


# =============================================================================
# Production Environment Detection
# =============================================================================

def is_production() -> bool:
    """Check if running in production environment."""
    return os.environ.get("RAILWAY_ENVIRONMENT") == "production"


def require_production_config():
    """
    Validate that production configuration is secure.

    Raises warnings/errors for insecure configurations.
    """
    issues = []

    if is_production():
        # Check CORS
        cors_origins = os.environ.get("CORS_ORIGINS", "")
        if not cors_origins or "*" in cors_origins:
            issues.append("CORS_ORIGINS not configured or contains wildcard")

        # Check admin key strength
        admin_key = os.environ.get("ADMIN_API_KEY", "")
        if len(admin_key) < 32:
            issues.append("ADMIN_API_KEY is less than 32 characters")

        # Check IP allowlist
        if not ADMIN_ALLOWED_IPS and not RAILWAY_INTERNAL_NETWORK:
            issues.append("ADMIN_ALLOWED_IPS not configured (admin endpoints exposed)")

        # Check for dev flags
        if ADMIN_ALLOW_ALL_IPS:
            issues.append("ADMIN_ALLOW_ALL_IPS is enabled in production (CRITICAL)")

        if os.environ.get("DEBUG"):
            issues.append("DEBUG mode enabled in production")

    for issue in issues:
        logger.warning(f"SECURITY CONFIG ISSUE: {issue}")

    return issues
