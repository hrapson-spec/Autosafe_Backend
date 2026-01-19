"""
Email Templates for AutoSafe.
Generates HTML and plain text email content for lead notifications.
"""
import os
from typing import List, Dict

# Base URL for outcome reporting links
BASE_URL = os.environ.get("BASE_URL", "https://autosafe.co.uk")


def generate_lead_email(
    garage_name: str,
    lead_email: str,
    lead_phone: str,
    lead_postcode: str,
    distance_miles: float,
    vehicle_make: str,
    vehicle_model: str,
    vehicle_year: int,
    failure_risk: float,
    reliability_score: int,
    top_risks: List[str],
    assignment_id: str,
) -> Dict[str, str]:
    """
    Generate lead notification email for a garage.

    Returns:
        Dict with 'subject', 'html', and 'text' keys
    """
    # Determine risk level
    if failure_risk >= 0.35:
        risk_level = "HIGH"
        risk_color = "#DC2626"
        urgency = "This vehicle likely needs attention before its MOT."
    elif failure_risk >= 0.20:
        risk_level = "MODERATE"
        risk_color = "#D97706"
        urgency = "A pre-MOT inspection would benefit this customer."
    else:
        risk_level = "LOW"
        risk_color = "#059669"
        urgency = "A routine check-up could prevent future issues."

    # Format top risks as HTML table rows
    risk_items = ""
    for i, risk in enumerate(top_risks[:3], 1):
        risk_items += f"<tr><td style='padding: 8px 0; border-bottom: 1px solid #E5E7EB;'>{i}. {risk.title()}</td></tr>"

    if not risk_items:
        risk_items = "<tr><td style='color: #059669;'>No major concerns identified</td></tr>"

    # Format contact info
    contact_html = f"<strong>{lead_email}</strong>"
    if lead_phone:
        contact_html += f"<br><strong>{lead_phone}</strong>"

    subject = f"New Lead: {vehicle_year} {vehicle_make} {vehicle_model} - {distance_miles} miles away"

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
            <td style="padding: 24px; background-color: #1E293B; text-align: center;">
                <h1 style="margin: 0; color: #FFFFFF; font-size: 24px;">AutoSafe</h1>
                <p style="margin: 8px 0 0 0; color: #94A3B8; font-size: 14px;">New Lead for {garage_name}</p>
            </td>
        </tr>

        <!-- Risk Banner -->
        <tr>
            <td style="padding: 20px 24px; background-color: {risk_color}10; border-left: 4px solid {risk_color};">
                <p style="margin: 0; font-size: 14px; color: {risk_color}; font-weight: 600;">
                    {risk_level} RISK VEHICLE
                </p>
                <p style="margin: 4px 0 0 0; font-size: 13px; color: #64748B;">
                    {urgency}
                </p>
            </td>
        </tr>

        <!-- Vehicle Info -->
        <tr>
            <td style="padding: 24px;">
                <h2 style="margin: 0 0 16px 0; font-size: 20px; color: #1E293B;">
                    {vehicle_year} {vehicle_make} {vehicle_model}
                </h2>

                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 20px;">
                    <tr>
                        <td style="padding: 12px; background-color: #F8FAFC; border-radius: 8px;" width="50%">
                            <p style="margin: 0; font-size: 12px; color: #64748B;">Reliability Score</p>
                            <p style="margin: 4px 0 0 0; font-size: 24px; font-weight: 700; color: #1E293B;">{reliability_score}/100</p>
                        </td>
                        <td width="16"></td>
                        <td style="padding: 12px; background-color: #F8FAFC; border-radius: 8px;" width="50%">
                            <p style="margin: 0; font-size: 12px; color: #64748B;">Distance</p>
                            <p style="margin: 4px 0 0 0; font-size: 24px; font-weight: 700; color: #1E293B;">{distance_miles} mi</p>
                        </td>
                    </tr>
                </table>

                <!-- Areas of Concern -->
                <h3 style="margin: 24px 0 12px 0; font-size: 14px; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em;">
                    Predicted Areas of Concern
                </h3>
                <table width="100%" cellpadding="0" cellspacing="0">
                    {risk_items}
                </table>

                <!-- Customer Contact -->
                <h3 style="margin: 24px 0 12px 0; font-size: 14px; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em;">
                    Customer Contact
                </h3>
                <p style="margin: 0; font-size: 14px; color: #1E293B;">
                    {contact_html}
                </p>
                <p style="margin: 8px 0 0 0; font-size: 14px; color: #64748B;">
                    Location: <strong style="color: #1E293B;">{lead_postcode}</strong>
                </p>
            </td>
        </tr>

        <!-- CTA -->
        <tr>
            <td style="padding: 0 24px 24px 24px;">
                <p style="margin: 0 0 16px 0; font-size: 14px; color: #64748B;">
                    This customer has checked their vehicle and knows they may need work done. They're actively looking for a trusted garage.
                </p>
            </td>
        </tr>

        <!-- Report Outcome -->
        <tr>
            <td style="padding: 24px; background-color: #F8FAFC; border-top: 1px solid #E5E7EB;">
                <p style="margin: 0 0 12px 0; font-size: 14px; color: #64748B;">
                    Did you win this job? Let us know to help us send you better leads.
                </p>
                <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=won"
                   style="display: inline-block; margin-right: 8px; padding: 8px 16px; background-color: #059669; color: #FFFFFF; text-decoration: none; border-radius: 6px; font-size: 13px;">
                    Won Job
                </a>
                <a href="{BASE_URL}/api/garage/outcome/{assignment_id}?result=lost"
                   style="display: inline-block; padding: 8px 16px; background-color: #E5E7EB; color: #64748B; text-decoration: none; border-radius: 6px; font-size: 13px;">
                    Didn't Convert
                </a>
            </td>
        </tr>

        <!-- Footer -->
        <tr>
            <td style="padding: 24px; text-align: center; border-top: 1px solid #E5E7EB;">
                <p style="margin: 0; font-size: 12px; color: #94A3B8;">
                    You're receiving this because you're a registered AutoSafe partner.
                </p>
            </td>
        </tr>
    </table>
</body>
</html>
"""

    # Plain text version
    text_body = f"""
New Lead from AutoSafe
======================

VEHICLE: {vehicle_year} {vehicle_make} {vehicle_model}
RISK LEVEL: {risk_level}
RELIABILITY SCORE: {reliability_score}/100
DISTANCE: {distance_miles} miles from your garage

AREAS OF CONCERN:
{chr(10).join(f"- {risk.title()}" for risk in top_risks[:3]) or "No major concerns identified"}

CUSTOMER CONTACT:
Email: {lead_email}
{f"Phone: {lead_phone}" if lead_phone else ""}
Location: {lead_postcode}

{urgency}

---
To report the outcome of this lead:
Won: {BASE_URL}/api/garage/outcome/{assignment_id}?result=won
Lost: {BASE_URL}/api/garage/outcome/{assignment_id}?result=lost
"""

    return {
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
