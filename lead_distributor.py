"""
Lead Distribution Orchestrator for AutoSafe.
Coordinates matching leads to garages and sending email notifications.
"""
import logging
from typing import Optional, List

import database as db
from lead_matcher import find_matching_garages, MatchedGarage
from email_service import send_email, is_configured as email_configured
from email_templates import generate_lead_email

logger = logging.getLogger(__name__)


async def distribute_lead(lead_id: str) -> dict:
    """
    Distribute a lead to matching garages.

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

    # Extract lead data
    top_risks = lead.get('top_risks') or []
    if isinstance(top_risks, str):
        import json
        top_risks = json.loads(top_risks)

    # Send email to each matched garage
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
            lead_email=lead.get('email', ''),
            lead_phone=lead.get('phone', ''),
            lead_postcode=lead['postcode'],
            distance_miles=garage.distance_miles,
            vehicle_make=lead.get('vehicle_make', 'Unknown'),
            vehicle_model=lead.get('vehicle_model', 'Unknown'),
            vehicle_year=lead.get('vehicle_year') or 0,
            failure_risk=lead.get('failure_risk') or 0,
            reliability_score=lead.get('reliability_score') or 0,
            top_risks=top_risks,
            assignment_id=assignment_id,
        )

        # Send email
        sent = await send_email(
            to_email=garage.email,
            subject=email_content['subject'],
            html_body=email_content['html'],
            text_body=email_content['text'],
            tags={
                "lead_id": lead_id,
                "garage_id": garage.garage_id,
                "assignment_id": assignment_id,
            }
        )

        if sent:
            result["emails_sent"] += 1
            await db.increment_garage_leads_received(garage.garage_id)
            logger.info(f"Lead {lead_id} sent to {garage.name} ({garage.email})")
        else:
            logger.error(f"Failed to send lead {lead_id} to {garage.email}")

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
