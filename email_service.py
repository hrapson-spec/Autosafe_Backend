"""
Email Service for AutoSafe.
Uses Resend for transactional email delivery.

Features:
- Singleton HTTP client for connection reuse (reduces latency)
- Dry run mode for testing without sending real emails
- PII masking in logs
"""
import os
import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """Mask email for logging to protect PII: john@example.com -> j***@example.com"""
    if not email or '@' not in email:
        return '***'
    local, domain = email.rsplit('@', 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


# Support multiple variable name formats
RESEND_API_KEY = (
    os.environ.get("RESEND_API_KEY") or
    os.environ.get("Resend API Key") or
    os.environ.get("Resend_API_Key")
)
EMAIL_FROM = (
    os.environ.get("EMAIL_FROM") or
    os.environ.get("Email From") or
    os.environ.get("Email_From") or
    "onboarding@resend.dev"
)
RESEND_API_URL = "https://api.resend.com/emails"

# Dry run mode - logs emails without sending (for testing in prod/staging)
EMAIL_DRY_RUN = os.environ.get("EMAIL_DRY_RUN", "false").lower() == "true"

# Singleton HTTP client for connection reuse
# This reduces latency and CPU overhead by reusing SSL connections
_email_client: Optional[httpx.AsyncClient] = None


def _get_email_client() -> httpx.AsyncClient:
    """Get or create the singleton HTTP client."""
    global _email_client
    if _email_client is None:
        _email_client = httpx.AsyncClient(timeout=10.0)
    return _email_client


async def close_email_client():
    """Close the singleton HTTP client. Call during app shutdown."""
    global _email_client
    if _email_client is not None:
        await _email_client.aclose()
        _email_client = None
        logger.info("Email client closed")


async def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
    tags: Optional[dict] = None
) -> bool:
    """
    Send an email via Resend.

    Args:
        to_email: Recipient email address
        subject: Email subject
        html_body: HTML content
        text_body: Plain text content (optional)
        tags: Optional dict of tags for tracking

    Returns:
        True if sent successfully, False otherwise
    """
    # Dry run mode - log but don't send
    if EMAIL_DRY_RUN:
        logger.info(f"[DRY RUN] Would send email to {_mask_email(to_email)}: {subject}")
        logger.debug(f"[DRY RUN] HTML Content: {html_body[:200]}...")
        return True

    if not RESEND_API_KEY:
        logger.error("RESEND_API_KEY not configured - email not sent")
        return False

    payload = {
        "from": f"AutoSafe Leads <{EMAIL_FROM}>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }

    if text_body:
        payload["text"] = text_body

    if tags:
        payload["tags"] = [{"name": k, "value": v} for k, v in tags.items()]

    try:
        client = _get_email_client()
        response = await client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

        if response.status_code == 200:
            logger.info(f"Email sent to {_mask_email(to_email)}: {subject}")
            return True
        else:
            logger.error(f"Email send failed: {response.status_code} - {response.text}")
            return False

    except httpx.TimeoutException:
        logger.error(f"Timeout sending email to {_mask_email(to_email)}")
        return False
    except Exception as e:
        logger.error(f"Email send exception: {e}")
        return False


def is_configured() -> bool:
    """Check if email service is configured."""
    return bool(RESEND_API_KEY) or EMAIL_DRY_RUN
