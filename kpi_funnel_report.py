"""
KPI Funnel Report
==================

Queries existing risk_checks and leads tables to calculate key funnel metrics.
Run daily via cron or ad-hoc for business reporting.

Usage:
    python kpi_funnel_report.py              # Last 7 days
    python kpi_funnel_report.py --days 30    # Last 30 days
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

import database as db

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


async def generate_report(days: int = 7):
    """Generate funnel KPI report from existing database tables."""
    pool = await db.get_pool()
    if not pool:
        logger.error("No database connection available")
        return

    since = datetime.now() - timedelta(days=days)

    async with pool.acquire() as conn:
        # 1. Risk checks (top of funnel)
        risk_checks = await conn.fetchrow(
            """SELECT COUNT(*) as total,
                      COUNT(DISTINCT registration) as unique_vehicles,
                      COUNT(CASE WHEN prediction_source = 'dvsa' THEN 1 END) as dvsa_predictions,
                      COUNT(CASE WHEN prediction_source = 'lookup' THEN 1 END) as lookup_predictions,
                      COUNT(CASE WHEN prediction_source = 'fallback' THEN 1 END) as fallback_predictions
               FROM risk_checks
               WHERE created_at >= $1""",
            since
        )

        # 2. All leads (middle of funnel)
        leads_total = await conn.fetchrow(
            """SELECT COUNT(*) as total,
                      COUNT(CASE WHEN lead_type = 'garage' THEN 1 END) as garage_leads,
                      COUNT(CASE WHEN lead_type = 'mot_reminder' THEN 1 END) as mot_reminders,
                      COUNT(CASE WHEN lead_type = 'report_email' THEN 1 END) as report_emails
               FROM leads
               WHERE created_at >= $1""",
            since
        )

        # 3. Lead distribution (bottom of funnel)
        assignments = await conn.fetchrow(
            """SELECT COUNT(*) as total,
                      COUNT(CASE WHEN outcome = 'won' THEN 1 END) as won,
                      COUNT(CASE WHEN outcome = 'lost' THEN 1 END) as lost,
                      COUNT(CASE WHEN outcome = 'no_response' THEN 1 END) as no_response,
                      COUNT(CASE WHEN outcome IS NULL THEN 1 END) as pending
               FROM lead_assignments
               WHERE email_sent_at >= $1""",
            since
        )

        # 4. UTM source breakdown
        utm_breakdown = await conn.fetch(
            """SELECT COALESCE(utm_source, 'direct') as source,
                      COUNT(*) as checks
               FROM risk_checks
               WHERE created_at >= $1
               GROUP BY utm_source
               ORDER BY checks DESC
               LIMIT 10""",
            since
        )

        # 5. MOT reminder stats
        reminder_stats = await conn.fetchrow(
            """SELECT COUNT(*) as total_signups,
                      COUNT(CASE WHEN reminder_28d_sent_at IS NOT NULL THEN 1 END) as reminders_sent,
                      COUNT(CASE WHEN unsubscribed_at IS NOT NULL THEN 1 END) as unsubscribed
               FROM leads
               WHERE lead_type = 'mot_reminder'""",
        )

    # Print report
    print(f"\n{'='*60}")
    print(f"  AUTOSAFE KPI FUNNEL REPORT")
    print(f"  Period: Last {days} days (since {since.strftime('%Y-%m-%d')})")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    print(f"1. TOP OF FUNNEL - Risk Checks")
    print(f"   Total checks:       {risk_checks['total']:,}")
    print(f"   Unique vehicles:    {risk_checks['unique_vehicles']:,}")
    print(f"   DVSA predictions:   {risk_checks['dvsa_predictions']:,}")
    print(f"   Lookup fallbacks:   {risk_checks['lookup_predictions']:,}")
    print(f"   Population avg:     {risk_checks['fallback_predictions']:,}")
    print()

    print(f"2. MIDDLE OF FUNNEL - Leads")
    print(f"   Total leads:        {leads_total['total']:,}")
    print(f"   Garage leads:       {leads_total['garage_leads']:,}")
    print(f"   MOT reminders:      {leads_total['mot_reminders']:,}")
    print(f"   Report emails:      {leads_total['report_emails']:,}")

    if risk_checks['total'] > 0:
        lead_rate = leads_total['total'] / risk_checks['total'] * 100
        print(f"   Check->Lead rate:   {lead_rate:.1f}%")
    print()

    print(f"3. BOTTOM OF FUNNEL - Lead Distribution")
    print(f"   Assignments sent:   {assignments['total']:,}")
    print(f"   Won:                {assignments['won']:,}")
    print(f"   Lost:               {assignments['lost']:,}")
    print(f"   No response:        {assignments['no_response']:,}")
    print(f"   Pending:            {assignments['pending']:,}")

    if assignments['total'] > 0:
        won_rate = assignments['won'] / assignments['total'] * 100
        print(f"   Win rate:           {won_rate:.1f}%")
    print()

    print(f"4. TRAFFIC SOURCES")
    for row in utm_breakdown:
        print(f"   {row['source']:20s} {row['checks']:,}")
    print()

    print(f"5. MOT REMINDERS (all time)")
    if reminder_stats:
        print(f"   Total signups:      {reminder_stats['total_signups']:,}")
        print(f"   Reminders sent:     {reminder_stats['reminders_sent']:,}")
        print(f"   Unsubscribed:       {reminder_stats['unsubscribed']:,}")
    print()

    print(f"{'='*60}\n")


async def main():
    days = 7
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        if idx + 1 < len(sys.argv):
            days = int(sys.argv[idx + 1])

    try:
        await generate_report(days)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
