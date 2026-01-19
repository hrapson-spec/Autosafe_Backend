"""
Email Service for AutoSafe.
Uses Resend for transactional email delivery.
"""
import os
import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "leads@autosafe.co.uk")
RESEND_API_URL = "https://api.resend.com/emails"


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
        async with httpx.AsyncClient() as client:
            response = await client.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10.0,
            )

            if response.status_code == 200:
                logger.info(f"Email sent to {to_email}: {subject}")
                return True
            else:
                logger.error(f"Email send failed: {response.status_code} - {response.text}")
                return False

    except httpx.TimeoutException:
        logger.error(f"Timeout sending email to {to_email}")
        return False
    except Exception as e:
        logger.error(f"Email send exception: {e}")
        return False


def is_configured() -> bool:
    """Check if email service is configured."""
    return bool(RESEND_API_KEY)
