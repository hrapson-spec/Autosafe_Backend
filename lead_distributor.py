"""
Lead Distribution Orchestrator for AutoSafe.
Coordinates matching leads to garages and sending email notifications.

Uses asyncio.gather for parallel email sending to improve performance.
"""
import asyncio
import logging
import os
from typing import Optional, List, Tuple
from dataclasses import dataclass


def _mask_email(email: str) -> str:
    """Mask email for logging to protect PII: john@example.com -> j***@example.com"""
    if not email or '@' not in email:
        return '***'
    local, domain = email.rsplit('@', 1)
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


# Maximum number of garages to notify per lead (prevents spam/quota exhaustion)
MAX_GARAGES_PER_LEAD = int(os.environ.get("MAX_GARAGES_PER_LEAD", "3"))

import database as db
from lead_matcher import find_matching_garages, MatchedGarage
from email_service import send_email, is_configured as email_configured
from email_templates import generate_lead_email

logger = logging.getLogger(__name__)


@dataclass
class EmailTask:
    """Represents an email task to be sent."""
    garage: MatchedGarage
    assignment_id: str
    email_content: dict


async def _send_single_email(task: EmailTask, lead_id: str) -> Tuple[bool, EmailTask]:
    """
    Send a single email and return the result.

    Returns:
        Tuple of (success: bool, task: EmailTask)
    """
    try:
        sent = await send_email(
            to_email=task.garage.email,
            subject=task.email_content['subject'],
            html_body=task.email_content['html'],
            text_body=task.email_content['text'],
            tags={
                "lead_id": lead_id,
                "garage_id": task.garage.garage_id,
                "assignment_id": task.assignment_id,
            }
        )
        return (sent, task)
    except Exception as e:
        logger.error(f"Exception sending email to {_mask_email(task.garage.email)}: {e}")
        return (False, task)


async def distribute_lead(lead_id: str) -> dict:
    """
    Distribute a lead to matching garages.

    Uses asyncio.gather to send emails in parallel for improved performance.

    Args:
        lead_id: The lead's UUID

    Returns:
        Dict with distribution results:
        {
            "success": bool,
            "garages_matched": int,
            "emails_sent": int,
            "error": str or None
        }
    """
    result = {
        "success": False,
        "garages_matched": 0,
        "emails_sent": 0,
        "error": None
    }

    # Get lead details
    lead = await db.get_lead_by_id(lead_id)
    if not lead:
        result["error"] = f"Lead not found: {lead_id}"
        logger.error(result["error"])
        return result

    # Check if already distributed
    if lead.get('distribution_status') == 'distributed':
        result["error"] = "Lead already distributed"
        logger.warning(f"Lead {lead_id} already distributed")
        return result

    # Check if email is configured
    if not email_configured():
        result["error"] = "Email service not configured (RESEND_API_KEY missing)"
        logger.error(result["error"])
        # Mark as failed
        await db.update_lead_distribution_status(lead_id, 'email_not_configured')
        return result

    # Find matching garages
    garages = await find_matching_garages(lead['postcode'])
    result["garages_matched"] = len(garages)

    if not garages:
        result["error"] = f"No garages found near {lead['postcode']}"
        logger.warning(result["error"])
        await db.update_lead_distribution_status(lead_id, 'no_garage_found')
        return result

    # Limit number of garages to prevent spam/quota exhaustion
    if len(garages) > MAX_GARAGES_PER_LEAD:
        logger.info(f"Limiting garages from {len(garages)} to {MAX_GARAGES_PER_LEAD} for lead {lead_id}")
        garages = garages[:MAX_GARAGES_PER_LEAD]

    # Extract lead data
    top_risks = lead.get('top_risks') or []
    if isinstance(top_risks, str):
        import json
        top_risks = json.loads(top_risks)

    # Phase 1: Create all assignment records and prepare email tasks
    # (Sequential - each needs unique assignment_id for tracking)
    email_tasks: List[EmailTask] = []

    for garage in garages:
        # Create assignment record first (to get assignment_id for tracking links)
        assignment_id = await db.create_lead_assignment(
            lead_id=lead_id,
            garage_id=garage.garage_id,
            distance_miles=garage.distance_miles
        )

        if not assignment_id:
            logger.error(f"Failed to create assignment for garage {garage.garage_id}")
            continue

        # Generate email content
        email_content = generate_lead_email(
            garage_name=garage.name,
            lead_name=lead.get('name'),
            lead_email=lead.get('email', ''),
            lead_phone=lead.get('phone'),
            lead_postcode=lead['postcode'],
            distance_miles=garage.distance_miles,
            vehicle_make=lead.get('vehicle_make', 'Unknown'),
            vehicle_model=lead.get('vehicle_model', 'Unknown'),
            vehicle_year=lead.get('vehicle_year') or 0,
            failure_risk=lead.get('failure_risk') or 0,
            reliability_score=lead.get('reliability_score') or 0,
            top_risks=top_risks,
            assignment_id=assignment_id,
            garages_count=len(garages),
        )

        email_tasks.append(EmailTask(
            garage=garage,
            assignment_id=assignment_id,
            email_content=email_content
        ))

    if not email_tasks:
        result["error"] = "Failed to create any assignment records"
        await db.update_lead_distribution_status(lead_id, 'assignment_failed')
        return result

    # Phase 2: Send all emails in parallel using asyncio.gather
    send_coroutines = [
        _send_single_email(task, lead_id)
        for task in email_tasks
    ]

    # Wait for all emails to be sent (or fail)
    send_results = await asyncio.gather(*send_coroutines, return_exceptions=True)

    # Phase 3: Process results and update database
    successful_tasks: List[EmailTask] = []

    for send_result in send_results:
        if isinstance(send_result, Exception):
            logger.error(f"Exception in email send task: {send_result}")
            continue

        sent, task = send_result
        if sent:
            successful_tasks.append(task)
            logger.info(f"Lead {lead_id} sent to {task.garage.name} ({_mask_email(task.garage.email)})")
        else:
            logger.error(f"Failed to send lead {lead_id} to {_mask_email(task.garage.email)}")

    # Update database for successful sends (can also be parallelized)
    if successful_tasks:
        increment_coroutines = [
            db.increment_garage_leads_received(task.garage.garage_id)
            for task in successful_tasks
        ]
        await asyncio.gather(*increment_coroutines, return_exceptions=True)

    result["emails_sent"] = len(successful_tasks)

    # Update lead status
    if result["emails_sent"] > 0:
        await db.update_lead_distribution_status(lead_id, 'distributed')
        result["success"] = True
        logger.info(f"Lead {lead_id} distributed to {result['emails_sent']} garage(s)")
    else:
        await db.update_lead_distribution_status(lead_id, 'email_failed')
        result["error"] = "Failed to send any emails"

    return result


async def retry_failed_distributions() -> dict:
    """
    Retry distributing leads that failed previously.

    Returns:
        Dict with retry results
    """
    # This could be called by a scheduled job
    # For now, just a placeholder
    pass
