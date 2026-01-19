"""
Email Templates for AutoSafe.
Generates HTML and plain text email content for lead notifications.
"""
import os
from typing import List, Dict, Optional

# Base URL for outcome reporting links
BASE_URL = os.environ.get("BASE_URL", "https://autosafe.co.uk")


def generate_lead_email(
    garage_name: str,
    lead_name: Optional[str],
    lead_email: str,
    lead_phone: Optional[str],
    lead_postcode: str,
    distance_miles: float,
    vehicle_make: str,
    vehicle_model: str,
    vehicle_year: int,
    failure_risk: float,
    reliability_score: int,
    top_risks: List[str],
    assignment_id: str,
    risk_percentages: Optional[Dict[str, float]] = None,
    estimated_job_min: int = 150,
    estimated_job_max: int = 400,
    garages_count: int = 3,
    leads_remaining: int = 5,
    max_leads_per_month: int = 5,
) -> Dict[str, str]:
    """
    Generate lead notification email for a garage.

    Args:
        garage_name: Name of the receiving garage
        lead_name: Customer name (optional)
        lead_email: Customer email
        lead_phone: Customer phone (optional)
        lead_postcode: Customer postcode
        distance_miles: Distance from garage to customer
        vehicle_make: Vehicle make
        vehicle_model: Vehicle model
        vehicle_year: Vehicle year
        failure_risk: Overall failure risk (0-1)
        reliability_score: Reliability score (0-100)
        top_risks: List of top risk areas
        assignment_id: UUID for outcome tracking
        risk_percentages: Dict mapping risk areas to percentages
        estimated_job_min: Minimum estimated job value
        estimated_job_max: Maximum estimated job value
        garages_count: Number of garages this lead was sent to
        leads_remaining: Free leads remaining this month
        max_leads_per_month: Maximum free leads per month

    Returns:
        Dict with 'subject', 'html', and 'text' keys
    """
    # Default risk percentages if not provided
    if risk_percentages is None:
        risk_percentages = {}

    # Determine primary concern for subject line
    primary_concern = top_risks[0].lower() if top_risks else "repair"

    # Subject line: Job value focused
    subject = f"New Lead: £{estimated_job_min}-{estimated_job_max} {primary_concern} job near you"

    # Determine risk level
    if failure_risk >= 0.35:
        risk_level = "HIGH"
        risk_color = "#DC2626"
    elif failure_risk >= 0.20:
        risk_level = "MODERATE"
        risk_color = "#D97706"
    else:
        risk_level = "LOW"
        risk_color = "#059669"

    # Build risk breakdown HTML with progress bars
    risk_breakdown_html = ""
    risk_components = [
        ("Brakes", risk_percentages.get("brakes", risk_percentages.get("Brakes", 0))),
        ("Suspension", risk_percentages.get("suspension", risk_percentages.get("Suspension", 0))),
        ("Tyres", risk_percentages.get("tyres", risk_percentages.get("Tyres", 0))),
        ("Steering", risk_percentages.get("steering", risk_percentages.get("Steering", 0))),
        ("Lights & Electrical", risk_percentages.get("lights", risk_percentages.get("Lamps_Reflectors_And_Electrical_Equipment", 0))),
        ("Body & Chassis", risk_percentages.get("body", risk_percentages.get("Body_Chassis_Structure", 0))),
    ]

    # Sort by risk percentage descending
    risk_components.sort(key=lambda x: x[1], reverse=True)

    for component, pct in risk_components:
        if pct > 0:
            pct_display = int(pct * 100) if pct < 1 else int(pct)
            bar_width = min(pct_display, 100)
            bar_color = "#DC2626" if pct_display >= 30 else ("#D97706" if pct_display >= 15 else "#059669")
            risk_breakdown_html += f"""
                <tr>
                    <td style="padding: 8px 0; width: 140px; font-size: 14px; color: #374151;">{component}</td>
                    <td style="padding: 8px 0;">
                        <div style="background-color: #E5E7EB; border-radius: 4px; height: 8px; width: 100%;">
                            <div style="background-color: {bar_color}; border-radius: 4px; height: 8px; width: {bar_width}%;"></div>
                        </div>
                    </td>
                    <td style="padding: 8px 0; width: 50px; text-align: right; font-size: 14px; font-weight: 600; color: #374151;">{pct_display}%</td>
                </tr>
            """

    # If no risk percentages, show top risks as list
    if not risk_breakdown_html:
        for risk in top_risks[:5]:
            risk_breakdown_html += f"""
                <tr>
                    <td colspan="3" style="padding: 6px 0; font-size: 14px; color: #374151;">• {risk.title()}</td>
                </tr>
            """

    # Customer name display
    customer_name_html = f"<p style='margin: 0 0 8px 0; font-size: 16px; font-weight: 600; color: #1E293B;'>{lead_name}</p>" if lead_name else ""

    # Phone display
    phone_html = f"""
        <tr>
            <td style="padding: 4px 0; font-size: 14px; color: #64748B; width: 30px;">📞</td>
            <td style="padding: 4px 0; font-size: 14px; color: #1E293B;">{lead_phone}</td>
        </tr>
    """ if lead_phone else ""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #F3F4F6;">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 600px; margin: 0 auto; background-color: #FFFFFF;">

        <!-- Header with Strong Branding -->
        <tr>
            <td style="padding: 24px; background: linear-gradient(135deg, #1E293B 0%, #334155 100%); text-align: center;">
                <h1 style="margin: 0; color: #FFFFFF; font-size: 28px; font-weight: 700;">AutoSafe</h1>
                <p style="margin: 8px 0 0 0; color: #94A3B8; font-size: 14px; letter-spacing: 0.5px;">AI-Powered MOT Predictions</p>
            </td>
        </tr>

        <!-- Free Lead Banner -->
        <tr>
            <td style="padding: 12px 24px; background-color: #DBEAFE; text-align: center;">
                <p style="margin: 0; font-size: 14px; color: #1E40AF; font-weight: 600;">
                    🎁 FREE LEAD ({leads_remaining} of {max_leads_per_month} remaining this month)
                </p>
            </td>
        </tr>

        <!-- Main Content -->
        <tr>
            <td style="padding: 24px;">

                <!-- Vehicle + Job Value -->
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 20px;">
                    <tr>
                        <td>
                            <p style="margin: 0 0 4px 0; font-size: 12px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">New Lead</p>
                            <h2 style="margin: 0 0 8px 0; font-size: 24px; color: #1E293B; font-weight: 700;">
                                {vehicle_year} {vehicle_make} {vehicle_model}
                            </h2>
                        </td>
                    </tr>
                </table>

                <!-- Job Value + Distance Cards -->
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 24px;">
                    <tr>
                        <td style="padding: 16px; background-color: #F0FDF4; border-radius: 8px; border: 1px solid #BBF7D0;" width="48%">
                            <p style="margin: 0; font-size: 12px; color: #166534; text-transform: uppercase; letter-spacing: 0.5px;">Estimated Job Value</p>
                            <p style="margin: 4px 0 0 0; font-size: 28px; font-weight: 700; color: #166534;">£{estimated_job_min}-{estimated_job_max}</p>
                        </td>
                        <td width="4%"></td>
                        <td style="padding: 16px; background-color: #F8FAFC; border-radius: 8px; border: 1px solid #E2E8F0;" width="48%">
                            <p style="margin: 0; font-size: 12px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">Distance</p>
                            <p style="margin: 4px 0 0 0; font-size: 28px; font-weight: 700; color: #1E293B;">{distance_miles} mi</p>
                        </td>
                    </tr>
                </table>

                <!-- Risk Breakdown -->
                <div style="margin-bottom: 24px;">
                    <h3 style="margin: 0 0 12px 0; font-size: 14px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">
                        Risk Breakdown
                    </h3>
                    <table width="100%" cellpadding="0" cellspacing="0">
                        {risk_breakdown_html}
                    </table>
                    <p style="margin: 12px 0 0 0; font-size: 13px; color: #64748B;">
                        Reliability Score: <strong style="color: #1E293B;">{reliability_score}/100</strong>
                    </p>
                </div>

                <!-- Why This Lead -->
                <div style="margin-bottom: 24px; padding: 16px; background-color: #FEF3C7; border-radius: 8px; border-left: 4px solid #F59E0B;">
                    <h3 style="margin: 0 0 8px 0; font-size: 14px; color: #92400E; text-transform: uppercase; letter-spacing: 0.5px;">
                        Why This Lead?
                    </h3>
                    <p style="margin: 0; font-size: 14px; color: #78350F;">
                        You're <strong>{distance_miles} miles</strong> from this customer. This lead was sent to <strong>{garages_count} garages</strong> in your area.
                    </p>
                </div>

                <!-- Customer Contact -->
                <div style="margin-bottom: 24px; padding: 16px; background-color: #F8FAFC; border-radius: 8px;">
                    <h3 style="margin: 0 0 12px 0; font-size: 14px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">
                        Customer Contact
                    </h3>
                    {customer_name_html}
                    <table width="100%" cellpadding="0" cellspacing="0">
                        <tr>
                            <td style="padding: 4px 0; font-size: 14px; color: #64748B; width: 30px;">📧</td>
                            <td style="padding: 4px 0; font-size: 14px; color: #1E293B;">{lead_email}</td>
                        </tr>
                        {phone_html}
                        <tr>
                            <td style="padding: 4px 0; font-size: 14px; color: #64748B; width: 30px;">📍</td>
                            <td style="padding: 4px 0; font-size: 14px; color: #1E293B;">{lead_postcode}</td>
                        </tr>
                    </table>
                </div>

            </td>
        </tr>

        <!-- Report Outcome -->
        <tr>
            <td style="padding: 24px; background-color: #F8FAFC; border-top: 1px solid #E5E7EB;">
                <p style="margin: 0 0 16px 0; font-size: 14px; color: #64748B;">
                    How did this lead go? Your feedback helps us send better leads.
                </p>
                <table cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="padding-right: 8px;">
                            <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=won"
                               style="display: inline-block; padding: 10px 20px; background-color: #059669; color: #FFFFFF; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">
                                Won Job
                            </a>
                        </td>
                        <td style="padding-right: 8px;">
                            <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=lost"
                               style="display: inline-block; padding: 10px 20px; background-color: #E5E7EB; color: #374151; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">
                                Lost
                            </a>
                        </td>
                        <td>
                            <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=no_response"
                               style="display: inline-block; padding: 10px 20px; background-color: #E5E7EB; color: #374151; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">
                                Couldn't Reach
                            </a>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>

        <!-- How We Know This -->
        <tr>
            <td style="padding: 20px 24px; background-color: #1E293B;">
                <h3 style="margin: 0 0 8px 0; font-size: 12px; color: #94A3B8; text-transform: uppercase; letter-spacing: 0.5px;">
                    How We Know This
                </h3>
                <p style="margin: 0; font-size: 14px; color: #E2E8F0;">
                    Our AI model analyzes <strong>10M+ real MOT results</strong> from DVSA to predict failures with <strong>85%+ accuracy</strong>.
                </p>
            </td>
        </tr>

        <!-- Footer -->
        <tr>
            <td style="padding: 24px; background-color: #F8FAFC; border-top: 1px solid #E5E7EB;">
                <p style="margin: 0 0 8px 0; font-size: 13px; color: #64748B;">
                    You're on the <strong>Free plan</strong> ({max_leads_per_month} leads/month)
                </p>
                <p style="margin: 0 0 16px 0; font-size: 13px;">
                    <a href="{BASE_URL}/pricing" style="color: #2563EB; text-decoration: none; font-weight: 600;">
                        Want unlimited leads? See our plans →
                    </a>
                </p>
                <p style="margin: 0 0 8px 0; font-size: 12px; color: #94A3B8;">
                    Questions? Reply to this email
                </p>
                <p style="margin: 0; font-size: 12px; color: #94A3B8;">
                    <a href="{BASE_URL}/dashboard" style="color: #64748B; text-decoration: none;">Dashboard</a> ·
                    <a href="{BASE_URL}/settings" style="color: #64748B; text-decoration: none;">Settings</a> ·
                    <a href="{BASE_URL}/support" style="color: #64748B; text-decoration: none;">Support</a>
                </p>
            </td>
        </tr>

    </table>
</body>
</html>
"""

    # Plain text version
    risk_list = "\n".join(f"  • {risk.title()}" for risk in top_risks[:5]) if top_risks else "  No major concerns identified"

    text_body = f"""
╔══════════════════════════════════════════════════════════════╗
║  AUTOSAFE - AI-Powered MOT Predictions                       ║
╚══════════════════════════════════════════════════════════════╝

🎁 FREE LEAD ({leads_remaining} of {max_leads_per_month} remaining this month)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEW LEAD: {vehicle_year} {vehicle_make} {vehicle_model}

ESTIMATED JOB VALUE: £{estimated_job_min}-{estimated_job_max}
DISTANCE: {distance_miles} miles from your garage
RELIABILITY SCORE: {reliability_score}/100

RISK AREAS:
{risk_list}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHY THIS LEAD?
You're {distance_miles} miles from this customer.
This lead was sent to {garages_count} garages in your area.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CUSTOMER CONTACT:
{f"Name: {lead_name}" if lead_name else ""}
Email: {lead_email}
{f"Phone: {lead_phone}" if lead_phone else ""}
Location: {lead_postcode}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REPORT OUTCOME:
• Won Job: {BASE_URL}/api/garage/outcome/{assignment_id}?result=won
• Lost: {BASE_URL}/api/garage/outcome/{assignment_id}?result=lost
• Couldn't Reach: {BASE_URL}/api/garage/outcome/{assignment_id}?result=no_response

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW WE KNOW THIS:
Our AI model analyzes 10M+ real MOT results from DVSA
to predict failures with 85%+ accuracy.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You're on the Free plan ({max_leads_per_month} leads/month)
Want unlimited leads? Visit {BASE_URL}/pricing

Questions? Reply to this email
"""

    return {
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
