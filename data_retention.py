"""
AutoSafe Data Retention Module
==============================

Implements automated data retention and deletion policies.

GDPR/Privacy requirements:
- Leads and lead_assignments must have enforced deletion schedules
- DVSA cache must expire and be non-exportable
- All retention periods must be documented and enforced
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# =============================================================================
# Retention Configuration
# =============================================================================

# Retention periods (in days) - configurable via environment
RETENTION_PERIODS = {
    # Leads containing personal data (email, phone, postcode)
    # Default: 90 days after creation (or after last activity)
    "leads": int(os.environ.get("RETENTION_LEADS_DAYS", "90")),

    # Lead assignments (links leads to garages)
    # Should match or be shorter than leads retention
    "lead_assignments": int(os.environ.get("RETENTION_ASSIGNMENTS_DAYS", "90")),

    # Audit logs (who accessed what)
    # Longer retention for security purposes
    "audit_logs": int(os.environ.get("RETENTION_AUDIT_DAYS", "365")),

    # DVSA cache (vehicle history)
    # Short retention - just for performance
    "dvsa_cache": 1,  # 24 hours, enforced by TTLCache

    # Inactive garages (soft-deleted)
    "inactive_garages": int(os.environ.get("RETENTION_INACTIVE_GARAGES_DAYS", "365")),
}


# =============================================================================
# Deletion Functions
# =============================================================================

async def delete_expired_leads(dry_run: bool = True) -> Dict:
    """
    Delete leads older than the retention period.

    Args:
        dry_run: If True, only report what would be deleted without actually deleting

    Returns:
        Dict with deletion results:
        {
            "deleted_count": int,
            "deleted_ids": List[str],
            "dry_run": bool,
            "retention_days": int
        }
    """
    import database as db

    result = {
        "deleted_count": 0,
        "deleted_ids": [],
        "dry_run": dry_run,
        "retention_days": RETENTION_PERIODS["leads"],
        "error": None
    }

    pool = await db.get_pool()
    if not pool:
        result["error"] = "Database not available"
        return result

    cutoff_date = datetime.utcnow() - timedelta(days=RETENTION_PERIODS["leads"])

    try:
        async with pool.acquire() as conn:
            if dry_run:
                # Just count what would be deleted
                rows = await conn.fetch(
                    """SELECT id FROM leads
                       WHERE created_at < $1
                       AND (distributed_at IS NULL OR distributed_at < $1)""",
                    cutoff_date
                )
                result["deleted_count"] = len(rows)
                result["deleted_ids"] = [str(row['id']) for row in rows[:100]]  # Sample
                logger.info(f"[DRY RUN] Would delete {len(rows)} leads older than {cutoff_date}")

            else:
                # First delete associated lead_assignments
                await conn.execute(
                    """DELETE FROM lead_assignments
                       WHERE lead_id IN (
                           SELECT id FROM leads
                           WHERE created_at < $1
                           AND (distributed_at IS NULL OR distributed_at < $1)
                       )""",
                    cutoff_date
                )

                # Then delete the leads
                deleted = await conn.fetch(
                    """DELETE FROM leads
                       WHERE created_at < $1
                       AND (distributed_at IS NULL OR distributed_at < $1)
                       RETURNING id""",
                    cutoff_date
                )
                result["deleted_count"] = len(deleted)
                result["deleted_ids"] = [str(row['id']) for row in deleted]
                logger.info(f"Deleted {len(deleted)} expired leads (retention: {RETENTION_PERIODS['leads']} days)")

    except Exception as e:
        logger.error(f"Failed to delete expired leads: {e}")
        result["error"] = str(e)

    return result


async def delete_orphaned_assignments(dry_run: bool = True) -> Dict:
    """
    Delete lead assignments that reference non-existent leads.

    Args:
        dry_run: If True, only report what would be deleted

    Returns:
        Dict with deletion results
    """
    import database as db

    result = {
        "deleted_count": 0,
        "dry_run": dry_run,
        "error": None
    }

    pool = await db.get_pool()
    if not pool:
        result["error"] = "Database not available"
        return result

    try:
        async with pool.acquire() as conn:
            if dry_run:
                count = await conn.fetchrow(
                    """SELECT COUNT(*) as count FROM lead_assignments la
                       WHERE NOT EXISTS (SELECT 1 FROM leads l WHERE l.id = la.lead_id)"""
                )
                result["deleted_count"] = count['count']
                logger.info(f"[DRY RUN] Would delete {count['count']} orphaned assignments")

            else:
                deleted = await conn.execute(
                    """DELETE FROM lead_assignments la
                       WHERE NOT EXISTS (SELECT 1 FROM leads l WHERE l.id = la.lead_id)"""
                )
                # Parse the "DELETE X" response
                result["deleted_count"] = int(deleted.split()[-1]) if deleted else 0
                logger.info(f"Deleted {result['deleted_count']} orphaned lead assignments")

    except Exception as e:
        logger.error(f"Failed to delete orphaned assignments: {e}")
        result["error"] = str(e)

    return result


async def anonymize_old_leads(dry_run: bool = True) -> Dict:
    """
    Anonymize leads older than retention period instead of deleting.

    This preserves aggregate data while removing PII.
    Useful if you want to keep statistics but comply with GDPR.

    Args:
        dry_run: If True, only report what would be anonymized

    Returns:
        Dict with anonymization results
    """
    import database as db

    result = {
        "anonymized_count": 0,
        "dry_run": dry_run,
        "retention_days": RETENTION_PERIODS["leads"],
        "error": None
    }

    pool = await db.get_pool()
    if not pool:
        result["error"] = "Database not available"
        return result

    cutoff_date = datetime.utcnow() - timedelta(days=RETENTION_PERIODS["leads"])

    try:
        async with pool.acquire() as conn:
            if dry_run:
                count = await conn.fetchrow(
                    """SELECT COUNT(*) as count FROM leads
                       WHERE created_at < $1 AND email NOT LIKE 'anonymized-%'""",
                    cutoff_date
                )
                result["anonymized_count"] = count['count']
                logger.info(f"[DRY RUN] Would anonymize {count['count']} leads")

            else:
                # Anonymize PII fields while preserving aggregate data
                updated = await conn.execute(
                    """UPDATE leads SET
                       email = 'anonymized-' || id::text || '@deleted.local',
                       name = NULL,
                       phone = NULL,
                       postcode = LEFT(postcode, LENGTH(postcode) - 3) || '***'
                       WHERE created_at < $1 AND email NOT LIKE 'anonymized-%'""",
                    cutoff_date
                )
                result["anonymized_count"] = int(updated.split()[-1]) if updated else 0
                logger.info(f"Anonymized {result['anonymized_count']} old leads")

    except Exception as e:
        logger.error(f"Failed to anonymize old leads: {e}")
        result["error"] = str(e)

    return result


async def cleanup_inactive_garages(dry_run: bool = True) -> Dict:
    """
    Delete garages that have been inactive for longer than retention period.

    Args:
        dry_run: If True, only report what would be deleted

    Returns:
        Dict with deletion results
    """
    import database as db

    result = {
        "deleted_count": 0,
        "dry_run": dry_run,
        "retention_days": RETENTION_PERIODS["inactive_garages"],
        "error": None
    }

    pool = await db.get_pool()
    if not pool:
        result["error"] = "Database not available"
        return result

    cutoff_date = datetime.utcnow() - timedelta(days=RETENTION_PERIODS["inactive_garages"])

    try:
        async with pool.acquire() as conn:
            if dry_run:
                count = await conn.fetchrow(
                    """SELECT COUNT(*) as count FROM garages
                       WHERE status = 'inactive' AND created_at < $1""",
                    cutoff_date
                )
                result["deleted_count"] = count['count']
                logger.info(f"[DRY RUN] Would delete {count['count']} inactive garages")

            else:
                # First remove any assignments to these garages
                await conn.execute(
                    """DELETE FROM lead_assignments
                       WHERE garage_id IN (
                           SELECT id FROM garages
                           WHERE status = 'inactive' AND created_at < $1
                       )""",
                    cutoff_date
                )

                deleted = await conn.fetch(
                    """DELETE FROM garages
                       WHERE status = 'inactive' AND created_at < $1
                       RETURNING id""",
                    cutoff_date
                )
                result["deleted_count"] = len(deleted)
                logger.info(f"Deleted {len(deleted)} inactive garages")

    except Exception as e:
        logger.error(f"Failed to cleanup inactive garages: {e}")
        result["error"] = str(e)

    return result


# =============================================================================
# DVSA Cache Controls
# =============================================================================

def clear_dvsa_cache():
    """
    Clear the DVSA response cache.

    This should be called:
    - On application shutdown
    - When a data subject requests deletion
    - Periodically to enforce TTL
    """
    try:
        from dvsa_client import get_dvsa_client
        client = get_dvsa_client()
        if hasattr(client, '_cache'):
            client._cache.clear()
            logger.info("DVSA cache cleared")
            return True
    except Exception as e:
        logger.error(f"Failed to clear DVSA cache: {e}")
    return False


def get_dvsa_cache_stats() -> Dict:
    """
    Get statistics about the DVSA cache.

    Returns:
        Dict with cache stats (size, maxsize, ttl, etc.)
    """
    try:
        from dvsa_client import get_dvsa_client
        client = get_dvsa_client()
        if hasattr(client, '_cache'):
            cache = client._cache
            return {
                "current_size": len(cache) if hasattr(cache, '__len__') else "unknown",
                "max_size": getattr(cache, 'maxsize', "unknown"),
                "ttl_seconds": getattr(cache, 'ttl', "unknown"),
                "exportable": False,  # By design, cache is not exportable
            }
    except Exception as e:
        logger.error(f"Failed to get DVSA cache stats: {e}")

    return {"error": "Cache not accessible"}


# =============================================================================
# Data Subject Deletion (GDPR Right to Erasure)
# =============================================================================

async def delete_data_subject(email: str, dry_run: bool = True) -> Dict:
    """
    Delete all data associated with a data subject (by email).

    This implements GDPR Article 17 - Right to Erasure.

    Args:
        email: The email address of the data subject
        dry_run: If True, only report what would be deleted

    Returns:
        Dict with deletion results
    """
    import database as db
    from security import redact_pii

    result = {
        "leads_deleted": 0,
        "assignments_deleted": 0,
        "garages_affected": 0,
        "dry_run": dry_run,
        "email_hash": redact_pii(email),  # Don't log full email
        "error": None
    }

    pool = await db.get_pool()
    if not pool:
        result["error"] = "Database not available"
        return result

    try:
        async with pool.acquire() as conn:
            # Find all leads for this email
            leads = await conn.fetch(
                "SELECT id FROM leads WHERE LOWER(email) = LOWER($1)",
                email
            )
            lead_ids = [row['id'] for row in leads]
            result["leads_deleted"] = len(lead_ids)

            if dry_run:
                if lead_ids:
                    # Count assignments
                    for lead_id in lead_ids:
                        count = await conn.fetchrow(
                            "SELECT COUNT(*) as count FROM lead_assignments WHERE lead_id = $1",
                            lead_id
                        )
                        result["assignments_deleted"] += count['count']

                logger.info(f"[DRY RUN] Would delete {result['leads_deleted']} leads, "
                           f"{result['assignments_deleted']} assignments for data subject")

            else:
                # Delete assignments first
                for lead_id in lead_ids:
                    await conn.execute(
                        "DELETE FROM lead_assignments WHERE lead_id = $1",
                        lead_id
                    )
                    result["assignments_deleted"] += 1

                # Delete leads
                await conn.execute(
                    "DELETE FROM leads WHERE LOWER(email) = LOWER($1)",
                    email
                )

                # Also check if they're a garage contact
                garage_check = await conn.fetch(
                    "SELECT id FROM garages WHERE LOWER(email) = LOWER($1)",
                    email
                )
                if garage_check:
                    # Anonymize garage record instead of deleting
                    await conn.execute(
                        """UPDATE garages SET
                           email = 'deleted-' || id::text || '@deleted.local',
                           contact_name = NULL,
                           phone = NULL,
                           status = 'inactive'
                           WHERE LOWER(email) = LOWER($1)""",
                        email
                    )
                    result["garages_affected"] = len(garage_check)

                logger.info(f"Data subject deletion completed: {result['leads_deleted']} leads, "
                           f"{result['assignments_deleted']} assignments deleted")

    except Exception as e:
        logger.error(f"Failed to delete data subject: {e}")
        result["error"] = str(e)

    # Clear any cached data
    clear_dvsa_cache()

    return result


# =============================================================================
# Scheduled Retention Job
# =============================================================================

async def run_retention_cleanup(dry_run: bool = True) -> Dict:
    """
    Run all retention cleanup tasks.

    This should be called periodically (e.g., daily cron job).

    Args:
        dry_run: If True, only report what would be done

    Returns:
        Dict with all cleanup results
    """
    logger.info(f"Starting retention cleanup (dry_run={dry_run})")

    results = {
        "timestamp": datetime.utcnow().isoformat(),
        "dry_run": dry_run,
        "tasks": {}
    }

    # Delete expired leads
    results["tasks"]["expired_leads"] = await delete_expired_leads(dry_run)

    # Delete orphaned assignments
    results["tasks"]["orphaned_assignments"] = await delete_orphaned_assignments(dry_run)

    # Cleanup inactive garages
    results["tasks"]["inactive_garages"] = await cleanup_inactive_garages(dry_run)

    # Clear DVSA cache (always runs, not affected by dry_run)
    clear_dvsa_cache()
    results["tasks"]["dvsa_cache_cleared"] = True

    logger.info(f"Retention cleanup completed: {results}")
    return results


# =============================================================================
# Retention Status Endpoint Data
# =============================================================================

async def get_retention_status() -> Dict:
    """
    Get current retention status and statistics.

    Returns:
        Dict with retention configuration and current data counts
    """
    import database as db

    status = {
        "retention_periods": RETENTION_PERIODS,
        "data_counts": {},
        "expiring_soon": {},
        "dvsa_cache": get_dvsa_cache_stats(),
    }

    pool = await db.get_pool()
    if not pool:
        status["error"] = "Database not available"
        return status

    try:
        async with pool.acquire() as conn:
            # Total counts
            leads_count = await conn.fetchrow("SELECT COUNT(*) as count FROM leads")
            status["data_counts"]["leads"] = leads_count['count']

            assignments_count = await conn.fetchrow("SELECT COUNT(*) as count FROM lead_assignments")
            status["data_counts"]["lead_assignments"] = assignments_count['count']

            garages_count = await conn.fetchrow("SELECT COUNT(*) as count FROM garages")
            status["data_counts"]["garages"] = garages_count['count']

            # Expiring soon (within 7 days of retention limit)
            leads_cutoff = datetime.utcnow() - timedelta(days=RETENTION_PERIODS["leads"] - 7)
            expiring = await conn.fetchrow(
                "SELECT COUNT(*) as count FROM leads WHERE created_at < $1",
                leads_cutoff
            )
            status["expiring_soon"]["leads"] = expiring['count']

    except Exception as e:
        logger.error(f"Failed to get retention status: {e}")
        status["error"] = str(e)

    return status
