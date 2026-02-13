"""
MOT Reminder Job Runner
========================

Sends 28-day MOT reminder emails to users who signed up for reminders.
Designed to be run daily via cron or Railway scheduled job.

Usage:
    python send_mot_reminders.py          # Send reminders (production)
    python send_mot_reminders.py --dry-run  # Preview without sending

Idempotent: uses reminder_28d_sent_at checkpoint column to avoid double-sends.
"""
import asyncio
import logging
import os
import sys
from datetime import date, timedelta

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

import database as db
from email_service import send_email, is_configured as email_configured
from email_templates import generate_mot_reminder_28d

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


async def get_due_reminders(dry_run: bool = False) -> list:
    """
    Query leads that need a 28-day reminder.

    Selects MOT reminder signups where:
    - lead_type = 'mot_reminder'
    - mot_expiry_date is between 25 and 31 days from now
    - reminder_28d_sent_at IS NULL (not already sent)
    - unsubscribed_at IS NULL (not unsubscribed)
    """
    pool = await db.get_pool()
    if not pool:
        logger.error("No database pool available")
        return []

    today = date.today()
    window_start = today + timedelta(days=25)
    window_end = today + timedelta(days=31)

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, email, registration, vehicle_make, vehicle_model,
                          vehicle_year, mot_expiry_date, failure_risk
                   FROM leads
                   WHERE lead_type = 'mot_reminder'
                     AND mot_expiry_date >= $1
                     AND mot_expiry_date <= $2
                     AND reminder_28d_sent_at IS NULL
                     AND (unsubscribed_at IS NULL)
                   ORDER BY mot_expiry_date ASC""",
                window_start, window_end
            )

            leads = []
            for row in rows:
                leads.append({
                    'id': str(row['id']),
                    'email': row['email'],
                    'registration': row['registration'],
                    'vehicle_make': row['vehicle_make'] or 'Unknown',
                    'vehicle_model': row['vehicle_model'] or 'Vehicle',
                    'vehicle_year': row['vehicle_year'] or 0,
                    'mot_expiry_date': row['mot_expiry_date'].isoformat() if row['mot_expiry_date'] else None,
                    'failure_risk': float(row['failure_risk']) if row['failure_risk'] else None,
                })

            logger.info(f"Found {len(leads)} reminders due (window: {window_start} to {window_end})")
            return leads

    except Exception as e:
        logger.error(f"Failed to query due reminders: {e}")
        return []


async def mark_reminder_sent(lead_id: str) -> bool:
    """Mark a reminder as sent by setting the checkpoint timestamp."""
    pool = await db.get_pool()
    if not pool:
        return False

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE leads SET reminder_28d_sent_at = NOW() WHERE id = $1",
                lead_id
            )
            return True
    except Exception as e:
        logger.error(f"Failed to mark reminder sent for {lead_id}: {e}")
        return False


async def send_reminders(dry_run: bool = False):
    """Main job: find due reminders and send them."""
    leads = await get_due_reminders(dry_run)

    if not leads:
        logger.info("No reminders due today")
        return {"sent": 0, "failed": 0, "skipped": 0}

    if not email_configured() and not dry_run:
        logger.error("Email service not configured, cannot send reminders")
        return {"sent": 0, "failed": 0, "skipped": len(leads)}

    sent = 0
    failed = 0
    skipped = 0

    for lead in leads:
        if not lead.get('mot_expiry_date'):
            logger.warning(f"Skipping lead {lead['id']}: no MOT expiry date")
            skipped += 1
            continue

        try:
            email_content = generate_mot_reminder_28d(
                email=lead['email'],
                registration=lead['registration'],
                vehicle_make=lead['vehicle_make'],
                vehicle_model=lead['vehicle_model'],
                vehicle_year=lead['vehicle_year'],
                mot_expiry_date=lead['mot_expiry_date'],
                failure_risk=lead.get('failure_risk'),
            )

            if dry_run:
                logger.info(f"[DRY RUN] Would send to {lead['email'][:3]}***: {email_content['subject']}")
                sent += 1
                continue

            success = await send_email(
                to_email=lead['email'],
                subject=email_content['subject'],
                html_body=email_content['html'],
                text_body=email_content['text'],
                tags={"type": "mot_reminder_28d", "lead_id": lead['id']},
            )

            if success:
                await mark_reminder_sent(lead['id'])
                sent += 1
                logger.info(f"Reminder sent: lead={lead['id']} reg={lead['registration']}")
            else:
                failed += 1
                logger.error(f"Reminder send failed: lead={lead['id']}")

        except Exception as e:
            failed += 1
            logger.error(f"Error sending reminder for lead {lead['id']}: {e}")

    result = {"sent": sent, "failed": failed, "skipped": skipped}
    logger.info(f"Reminder job complete: {result}")
    return result


async def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        logger.info("Running in DRY RUN mode - no emails will be sent")

    try:
        result = await send_reminders(dry_run)
        logger.info(f"Job finished: {result}")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
