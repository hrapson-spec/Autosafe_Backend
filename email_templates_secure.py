"""
Secure Email Templates for AutoSafe.
=====================================

PRIVACY-FIRST DESIGN:
- Minimal data in email body
- Customer contact info accessed via secure portal link
- Assignment ID used for tracking (not PII)

This replaces email_templates.py for production use.
"""
import os
from typing import List, Dict, Optional

# Base URL for portal and outcome links
BASE_URL = os.environ.get("BASE_URL", "https://autosafe.co.uk")


def generate_lead_email_minimal(
    garage_name: str,
    distance_miles: float,
    vehicle_make: str,
    vehicle_model: str,
    vehicle_year: int,
    failure_risk: float,
    top_risks: List[str],
    assignment_id: str,
    garage_id: str = "",
    estimated_job_min: int = 150,
    estimated_job_max: int = 400,
    garages_count: int = 3,
    leads_remaining: int = 5,
    max_leads_per_month: int = 5,
) -> Dict[str, str]:
    """
    Generate a MINIMAL lead notification email.

    PRIVACY DESIGN:
    - NO customer email in body
    - NO customer phone in body
    - NO customer name in body
    - Postcode shown as outcode only (e.g., "SW1A" not "SW1A 1AA")
    - All contact details accessed via secure portal link

    Args:
        garage_name: Name of the receiving garage
        distance_miles: Distance from garage to customer
        vehicle_make: Vehicle make
        vehicle_model: Vehicle model
        vehicle_year: Vehicle year
        failure_risk: Overall failure risk (0-1)
        top_risks: List of top risk areas
        assignment_id: UUID for tracking and portal access
        garage_id: UUID of the garage for unsubscribe link
        estimated_job_min: Minimum estimated job value
        estimated_job_max: Maximum estimated job value
        garages_count: Number of garages this lead was sent to
        leads_remaining: Free leads remaining this month
        max_leads_per_month: Maximum free leads per month

    Returns:
        Dict with 'subject', 'html', and 'text' keys
    """
    # Determine primary concern for subject line
    primary_concern = top_risks[0].lower() if top_risks else "repair"

    # Subject line: Job value focused (no PII)
    subject = f"New Lead: {vehicle_year} {vehicle_make} {vehicle_model} - {distance_miles} miles away"

    # Determine risk level
    if failure_risk >= 0.35:
        risk_level = "HIGH"
        risk_color = "#DC2626"
        risk_bg = "#FEF2F2"
    elif failure_risk >= 0.20:
        risk_level = "MODERATE"
        risk_color = "#D97706"
        risk_bg = "#FFFBEB"
    else:
        risk_level = "LOW"
        risk_color = "#059669"
        risk_bg = "#F0FDF4"

    # Portal link for viewing full lead details
    portal_link = f"{BASE_URL}/portal/lead/{assignment_id}"

    # Build risk areas list
    risk_items_html = ""
    for risk in top_risks[:4]:
        risk_items_html += f'<li style="margin: 4px 0; color: #374151;">{risk.title()}</li>'

    if not risk_items_html:
        risk_items_html = '<li style="margin: 4px 0; color: #374151;">General MOT preparation</li>'

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #F3F4F6;">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 600px; margin: 0 auto; background-color: #FFFFFF;">

        <!-- Header -->
        <tr>
            <td style="padding: 24px; background: linear-gradient(135deg, #1E293B 0%, #334155 100%); text-align: center;">
                <h1 style="margin: 0; color: #FFFFFF; font-size: 28px; font-weight: 700;">AutoSafe</h1>
                <p style="margin: 8px 0 0 0; color: #94A3B8; font-size: 14px;">New Lead Available</p>
            </td>
        </tr>

        <!-- Lead Count Banner -->
        <tr>
            <td style="padding: 12px 24px; background-color: #DBEAFE; text-align: center;">
                <p style="margin: 0; font-size: 14px; color: #1E40AF; font-weight: 600;">
                    Free lead ({leads_remaining} of {max_leads_per_month} remaining this month)
                </p>
            </td>
        </tr>

        <!-- Main Content -->
        <tr>
            <td style="padding: 24px;">

                <!-- Vehicle Info -->
                <div style="margin-bottom: 20px;">
                    <p style="margin: 0 0 4px 0; font-size: 12px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">Vehicle</p>
                    <h2 style="margin: 0; font-size: 24px; color: #1E293B; font-weight: 700;">
                        {vehicle_year} {vehicle_make} {vehicle_model}
                    </h2>
                </div>

                <!-- Key Stats -->
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 24px;">
                    <tr>
                        <td style="padding: 16px; background-color: #F0FDF4; border-radius: 8px; border: 1px solid #BBF7D0;" width="48%">
                            <p style="margin: 0; font-size: 12px; color: #166534; text-transform: uppercase;">Est. Job Value</p>
                            <p style="margin: 4px 0 0 0; font-size: 24px; font-weight: 700; color: #166534;">£{estimated_job_min}-{estimated_job_max}</p>
                        </td>
                        <td width="4%"></td>
                        <td style="padding: 16px; background-color: #F8FAFC; border-radius: 8px; border: 1px solid #E2E8F0;" width="48%">
                            <p style="margin: 0; font-size: 12px; color: #64748B; text-transform: uppercase;">Distance</p>
                            <p style="margin: 4px 0 0 0; font-size: 24px; font-weight: 700; color: #1E293B;">{distance_miles} mi</p>
                        </td>
                    </tr>
                </table>

                <!-- Risk Level Badge -->
                <div style="margin-bottom: 24px; padding: 16px; background-color: {risk_bg}; border-radius: 8px; border-left: 4px solid {risk_color};">
                    <p style="margin: 0 0 8px 0; font-size: 14px; color: {risk_color}; font-weight: 600;">
                        MOT Risk: {risk_level} ({int(failure_risk * 100)}% failure probability)
                    </p>
                    <p style="margin: 0; font-size: 13px; color: #64748B;">Areas of concern:</p>
                    <ul style="margin: 8px 0 0 0; padding-left: 20px;">
                        {risk_items_html}
                    </ul>
                </div>

                <!-- Competition Notice -->
                <div style="margin-bottom: 24px; padding: 12px 16px; background-color: #FEF3C7; border-radius: 8px;">
                    <p style="margin: 0; font-size: 14px; color: #92400E;">
                        <strong>This lead was sent to {garages_count} garages</strong> in your area. Contact the customer quickly for best results.
                    </p>
                </div>

                <!-- CTA Button -->
                <div style="text-align: center; margin-bottom: 24px;">
                    <a href="{portal_link}"
                       style="display: inline-block; padding: 16px 32px; background-color: #2563EB; color: #FFFFFF; text-decoration: none; border-radius: 8px; font-size: 16px; font-weight: 600;">
                        View Customer Details
                    </a>
                    <p style="margin: 12px 0 0 0; font-size: 13px; color: #64748B;">
                        Click to see contact info and full vehicle details
                    </p>
                </div>

            </td>
        </tr>

        <!-- Outcome Tracking -->
        <tr>
            <td style="padding: 24px; background-color: #F8FAFC; border-top: 1px solid #E5E7EB;">
                <p style="margin: 0 0 12px 0; font-size: 14px; color: #64748B;">
                    After contacting this customer, let us know how it went:
                </p>
                <table cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="padding-right: 8px;">
                            <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=won"
                               style="display: inline-block; padding: 10px 16px; background-color: #059669; color: #FFFFFF; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">
                                Won Job
                            </a>
                        </td>
                        <td style="padding-right: 8px;">
                            <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=lost"
                               style="display: inline-block; padding: 10px 16px; background-color: #E5E7EB; color: #374151; text-decoration: none; border-radius: 6px; font-size: 14px;">
                                Lost
                            </a>
                        </td>
                        <td>
                            <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=no_response"
                               style="display: inline-block; padding: 10px 16px; background-color: #E5E7EB; color: #374151; text-decoration: none; border-radius: 6px; font-size: 14px;">
                                No Response
                            </a>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>

        <!-- Footer -->
        <tr>
            <td style="padding: 20px 24px; background-color: #1E293B;">
                <p style="margin: 0; font-size: 12px; color: #94A3B8; text-align: center;">
                    AutoSafe | AI-Powered MOT Predictions<br>
                    <a href="{BASE_URL}/privacy" style="color: #64748B;">Privacy Policy</a> |
                    <a href="{BASE_URL}/api/garage/unsubscribe/{garage_id}" style="color: #64748B;">Unsubscribe</a>
                </p>
            </td>
        </tr>

    </table>
</body>
</html>
"""

    # Plain text version (also minimal)
    risk_list = "\n".join(f"  - {risk.title()}" for risk in top_risks[:4]) if top_risks else "  - General MOT preparation"

    text_body = f"""
AUTOSAFE - New Lead Available
=============================

Free lead ({leads_remaining} of {max_leads_per_month} remaining this month)

VEHICLE: {vehicle_year} {vehicle_make} {vehicle_model}

ESTIMATED JOB VALUE: £{estimated_job_min}-{estimated_job_max}
DISTANCE: {distance_miles} miles from your garage
MOT RISK: {risk_level} ({int(failure_risk * 100)}% failure probability)

AREAS OF CONCERN:
{risk_list}

This lead was sent to {garages_count} garages in your area.
Contact the customer quickly for best results.

---------------------------------------------

VIEW CUSTOMER CONTACT DETAILS:
{portal_link}

---------------------------------------------

REPORT OUTCOME:
- Won Job: {BASE_URL}/api/garage/outcome/{assignment_id}?result=won
- Lost: {BASE_URL}/api/garage/outcome/{assignment_id}?result=lost
- No Response: {BASE_URL}/api/garage/outcome/{assignment_id}?result=no_response

---------------------------------------------

AutoSafe | AI-Powered MOT Predictions
Privacy: {BASE_URL}/privacy
Unsubscribe: {BASE_URL}/api/garage/unsubscribe/{garage_id}
"""

    return {
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }


def generate_portal_page_html(
    lead_name: Optional[str],
    lead_email: str,
    lead_phone: Optional[str],
    lead_postcode: str,
    vehicle_make: str,
    vehicle_model: str,
    vehicle_year: int,
    failure_risk: float,
    reliability_score: int,
    top_risks: List[str],
    assignment_id: str,
    risk_percentages: Optional[Dict[str, float]] = None,
) -> str:
    """
    Generate HTML for the secure portal page where garages view lead details.

    This page is accessed via authenticated link from the email.
    Contains full customer contact information.
    """
    # Risk breakdown
    risk_breakdown_html = ""
    if risk_percentages:
        risk_components = [
            ("Brakes", risk_percentages.get("brakes", 0)),
            ("Suspension", risk_percentages.get("suspension", 0)),
            ("Tyres", risk_percentages.get("tyres", 0)),
            ("Steering", risk_percentages.get("steering", 0)),
            ("Lights", risk_percentages.get("lights", 0)),
            ("Body", risk_percentages.get("body", 0)),
        ]
        for component, pct in sorted(risk_components, key=lambda x: x[1], reverse=True):
            if pct > 0:
                pct_display = int(pct * 100) if pct < 1 else int(pct)
                risk_breakdown_html += f"""
                <div style="margin-bottom: 8px;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                        <span>{component}</span>
                        <span>{pct_display}%</span>
                    </div>
                    <div style="background: #E5E7EB; height: 8px; border-radius: 4px;">
                        <div style="background: #DC2626; height: 8px; border-radius: 4px; width: {min(pct_display, 100)}%;"></div>
                    </div>
                </div>
                """

    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Lead Details - AutoSafe</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #F3F4F6; margin: 0; padding: 20px; }}
        .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); overflow: hidden; }}
        .header {{ background: linear-gradient(135deg, #1E293B, #334155); color: white; padding: 24px; text-align: center; }}
        .content {{ padding: 24px; }}
        .section {{ margin-bottom: 24px; padding: 16px; background: #F8FAFC; border-radius: 8px; }}
        .section-title {{ font-size: 12px; color: #64748B; text-transform: uppercase; margin-bottom: 8px; }}
        .contact-item {{ display: flex; align-items: center; padding: 8px 0; border-bottom: 1px solid #E5E7EB; }}
        .contact-item:last-child {{ border-bottom: none; }}
        .btn {{ display: inline-block; padding: 12px 24px; background: #2563EB; color: white; text-decoration: none; border-radius: 6px; font-weight: 600; }}
        .btn-outline {{ background: transparent; border: 2px solid #2563EB; color: #2563EB; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">Lead Details</h1>
            <p style="margin: 8px 0 0 0; opacity: 0.8;">{vehicle_year} {vehicle_make} {vehicle_model}</p>
        </div>

        <div class="content">
            <div class="section">
                <div class="section-title">Customer Contact</div>
                <div class="contact-item">
                    <span style="width: 80px; color: #64748B;">Name:</span>
                    <strong>{lead_name or 'Not provided'}</strong>
                </div>
                <div class="contact-item">
                    <span style="width: 80px; color: #64748B;">Email:</span>
                    <a href="mailto:{lead_email}" style="color: #2563EB;">{lead_email}</a>
                </div>
                <div class="contact-item">
                    <span style="width: 80px; color: #64748B;">Phone:</span>
                    <a href="tel:{lead_phone or ''}" style="color: #2563EB;">{lead_phone or 'Not provided'}</a>
                </div>
                <div class="contact-item">
                    <span style="width: 80px; color: #64748B;">Location:</span>
                    <span>{lead_postcode}</span>
                </div>
            </div>

            <div class="section">
                <div class="section-title">Vehicle Risk Assessment</div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 16px;">
                    <div>
                        <div style="font-size: 36px; font-weight: 700; color: #DC2626;">{int(failure_risk * 100)}%</div>
                        <div style="color: #64748B;">Failure Risk</div>
                    </div>
                    <div style="text-align: right;">
                        <div style="font-size: 36px; font-weight: 700; color: #059669;">{reliability_score}</div>
                        <div style="color: #64748B;">Reliability Score</div>
                    </div>
                </div>
                {risk_breakdown_html or '<p style="color: #64748B;">Detailed risk breakdown not available</p>'}
            </div>

            <div style="text-align: center; margin-top: 24px;">
                <a href="mailto:{lead_email}" class="btn">Email Customer</a>
                {f'<a href="tel:{lead_phone}" class="btn btn-outline" style="margin-left: 12px;">Call Customer</a>' if lead_phone else ''}
            </div>

            <div style="margin-top: 24px; padding-top: 24px; border-top: 1px solid #E5E7EB; text-align: center;">
                <p style="color: #64748B; font-size: 14px; margin-bottom: 12px;">How did this lead go?</p>
                <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=won" style="color: #059669; margin: 0 12px;">Won</a>
                <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=lost" style="color: #64748B; margin: 0 12px;">Lost</a>
                <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=no_response" style="color: #64748B; margin: 0 12px;">No Response</a>
            </div>
        </div>
    </div>
</body>
</html>
"""
