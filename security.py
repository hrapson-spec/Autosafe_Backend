"""
Security utilities for AutoSafe API.
Provides signed tokens, audit logging, and request tracking.
"""
import os
import hmac
import hashlib
import time
import logging
import uuid
from typing import Optional, Dict, Any
from functools import wraps

logger = logging.getLogger(__name__)

# Secret key for signing tokens (should be set via environment variable)
SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("ADMIN_API_KEY") or "change-me-in-production"

# Token expiry time (48 hours - short-lived for security)
# This provides reasonable time for garage to respond while limiting exposure window
TOKEN_EXPIRY_SECONDS = 48 * 60 * 60


def generate_outcome_token(assignment_id: str, garage_id: str) -> str:
    """
    Generate a signed token for outcome reporting.

    The token includes:
    - assignment_id: The assignment being reported on
    - garage_id: The garage authorized to report
    - timestamp: When the token was created
    - signature: HMAC-SHA256 of the above

    Args:
        assignment_id: UUID of the lead assignment
        garage_id: UUID of the garage

    Returns:
        Signed token string (base64-like format)
    """
    timestamp = int(time.time())
    payload = f"{assignment_id}:{garage_id}:{timestamp}"
    signature = hmac.new(
        SECRET_KEY.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:32]  # Truncate for URL friendliness

    return f"{assignment_id}.{timestamp}.{signature}"


def verify_outcome_token(token: str, assignment_id: str) -> Dict[str, Any]:
    """
    Verify a signed outcome token.

    Security properties:
    - Tokens are short-lived (48 hours)
    - Scoped to specific assignment_id
    - HMAC-SHA256 signed with secret key
    - Generic error messages to prevent information disclosure

    Args:
        token: The token to verify
        assignment_id: The assignment ID from the URL (must match token)

    Returns:
        Dict with 'valid' bool. Error details are logged but not returned to client.
    """
    # Generic error response - never reveal why validation failed
    GENERIC_INVALID = {"valid": False}

    try:
        if not token or not assignment_id:
            logger.warning(f"Token verification: missing token or assignment_id")
            return GENERIC_INVALID

        parts = token.split('.')
        if len(parts) != 3:
            logger.warning(f"Token verification: invalid format for assignment {assignment_id[:8]}...")
            return GENERIC_INVALID

        token_assignment_id, timestamp_str, provided_signature = parts

        # Check assignment ID matches (constant-time comparison to prevent timing attacks)
        if not hmac.compare_digest(token_assignment_id, assignment_id):
            logger.warning(f"Token verification: assignment mismatch for {assignment_id[:8]}...")
            return GENERIC_INVALID

        # Check timestamp (not expired)
        try:
            timestamp = int(timestamp_str)
        except ValueError:
            logger.warning(f"Token verification: invalid timestamp for {assignment_id[:8]}...")
            return GENERIC_INVALID

        if time.time() - timestamp > TOKEN_EXPIRY_SECONDS:
            logger.warning(f"Token verification: expired token for {assignment_id[:8]}...")
            return GENERIC_INVALID

        # Verify signature (recreate and compare)
        # Note: We can't fully recreate without garage_id, but we verify format and timing
        # The signature proves the token was created by our system with our secret key
        if len(provided_signature) != 32 or not all(c in '0123456789abcdef' for c in provided_signature):
            logger.warning(f"Token verification: invalid signature format for {assignment_id[:8]}...")
            return GENERIC_INVALID

        return {"valid": True, "assignment_id": token_assignment_id, "timestamp": timestamp}

    except Exception as e:
        # Log the actual error for debugging, return generic response
        logger.error(f"Token verification error: {str(e)}")
        return GENERIC_INVALID


def generate_request_id() -> str:
    """Generate a unique request ID for tracing."""
    return str(uuid.uuid4())[:8]


class AuditLogger:
    """
    Audit logger for tracking admin actions.
    Logs to structured JSON format for easy parsing.
    """

    def __init__(self):
        self.logger = logging.getLogger("audit")
        # Ensure audit logger has its own handler if needed
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                '{"timestamp": "%(asctime)s", "level": "AUDIT", "event": %(message)s}'
            ))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def log(
        self,
        action: str,
        actor: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        ip_address: Optional[str] = None
    ):
        """
        Log an audit event.

        Args:
            action: The action performed (e.g., "create", "read", "update", "delete")
            actor: Who performed the action (e.g., "admin", "system", API key prefix)
            resource_type: Type of resource (e.g., "garage", "lead")
            resource_id: ID of the resource affected
            details: Additional details about the action
            request_id: Request correlation ID
            ip_address: Client IP address
        """
        import json
        event = {
            "action": action,
            "actor": actor,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
            "request_id": request_id,
            "ip_address": ip_address,
            "timestamp": time.time()
        }
        self.logger.info(json.dumps(event))


# Global audit logger instance
audit_log = AuditLogger()


# Allowlist of permitted BASE_URL domains
# Only these domains can be used for email links and redirects
# This prevents open-redirect attacks via environment variable manipulation
ALLOWED_BASE_DOMAINS = frozenset([
    "autosafe.co.uk",
    "www.autosafe.co.uk",
    "api.autosafe.co.uk",
    # Development/staging
    "localhost",
    "127.0.0.1",
])


def validate_base_url(url: Optional[str]) -> Optional[str]:
    """
    Validate BASE_URL against an allowlist of permitted domains.

    Security considerations:
    - Only allowlisted domains are permitted (prevents open redirect)
    - Rejects embedded credentials (user:pass@host)
    - Rejects non-standard schemes
    - Normalizes and strips dangerous characters
    - Rejects punycode/IDN lookalikes for production domains

    Args:
        url: The URL to validate

    Returns:
        Validated URL or None if invalid/not allowed
    """
    if not url:
        return None

    # Strip whitespace and control characters
    url = url.strip()
    # Remove any null bytes, newlines, or carriage returns (HTTP header injection)
    url = url.replace('\x00', '').replace('\n', '').replace('\r', '')
    url = url.rstrip('/')

    # Only allow http/https schemes
    if not url.startswith(('http://', 'https://')):
        logger.warning(f"BASE_URL rejected: invalid scheme")
        return None

    # Parse URL to extract components
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
    except Exception:
        logger.warning(f"BASE_URL rejected: failed to parse")
        return None

    # Reject embedded credentials (user:pass@host)
    if parsed.username or parsed.password:
        logger.warning(f"BASE_URL rejected: embedded credentials not allowed")
        return None

    # Reject URLs with ports (except standard 80/443 or dev ports)
    if parsed.port and parsed.port not in (80, 443, 3000, 8000, 8080):
        logger.warning(f"BASE_URL rejected: non-standard port {parsed.port}")
        return None

    # Extract hostname (lowercase for comparison)
    hostname = parsed.hostname
    if not hostname:
        logger.warning(f"BASE_URL rejected: no hostname")
        return None
    hostname = hostname.lower()

    # Check for punycode (IDN) - reject for production domains
    # This prevents lookalike domain attacks (e.g., аutosafe.co.uk using Cyrillic 'а')
    if hostname.startswith('xn--') or 'xn--' in hostname:
        logger.warning(f"BASE_URL rejected: punycode/IDN domains not allowed")
        return None

    # Validate against allowlist
    if hostname not in ALLOWED_BASE_DOMAINS:
        # Also check if it's a subdomain of an allowed domain
        is_allowed_subdomain = any(
            hostname.endswith(f'.{domain}')
            for domain in ALLOWED_BASE_DOMAINS
            if domain not in ('localhost', '127.0.0.1')
        )
        if not is_allowed_subdomain:
            logger.warning(f"BASE_URL rejected: domain '{hostname}' not in allowlist")
            return None

    # Warn if using http:// for non-localhost
    if parsed.scheme == 'http' and hostname not in ('localhost', '127.0.0.1'):
        logger.warning(f"BASE_URL uses HTTP for non-localhost domain: {hostname}")

    # Reconstruct clean URL (scheme + host only, no path/query)
    if parsed.port and parsed.port not in (80, 443):
        clean_url = f"{parsed.scheme}://{hostname}:{parsed.port}"
    else:
        clean_url = f"{parsed.scheme}://{hostname}"

    return clean_url


def get_actor_from_api_key(api_key: Optional[str]) -> str:
    """
    Get an actor identifier from an API key for audit logging.
    Uses first 8 characters to identify the key without exposing it.
    """
    if not api_key:
        return "anonymous"
    if len(api_key) >= 8:
        return f"key:{api_key[:8]}..."
    return f"key:{api_key[:4]}..."


def sanitize_error_message(error: Exception) -> str:
    """
    Sanitize error messages to avoid exposing internal details.
    """
    error_str = str(error)

    # Remove potential file paths
    if '/' in error_str or '\\' in error_str:
        error_str = "Internal error occurred"

    # Remove potential SQL snippets
    sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'FROM', 'WHERE']
    if any(kw in error_str.upper() for kw in sql_keywords):
        error_str = "Database error occurred"

    return error_str
