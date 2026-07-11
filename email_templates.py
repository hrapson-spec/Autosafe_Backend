"""
Email Templates for AutoSafe.
Generates HTML and plain text email content for lead notifications.

Uses Jinja2 templating with autoescape enabled to prevent XSS attacks.
All user-provided data is automatically HTML-escaped.
"""
import os
from typing import List, Dict, Optional
from jinja2 import Environment, BaseLoader, select_autoescape

# Base URL for outcome reporting links
BASE_URL = os.environ.get("BASE_URL", "https://www.autosafe.one")

# Create Jinja2 environment with autoescape enabled for HTML
# This automatically escapes dangerous characters like <, >, &, ", '
_jinja_env = Environment(
    loader=BaseLoader(),
    autoescape=select_autoescape(default=True, default_for_string=True)
)


def _get_risk_level(failure_risk: float) -> tuple:
    """Determine risk level and color from failure risk."""
    if failure_risk >= 0.35:
        return "HIGH", "#DC2626"
    elif failure_risk >= 0.20:
        return "MODERATE", "#D97706"
    else:
        return "LOW", "#059669"


_VEHICLE_COMPARISON_SCOPES = {"exact_band", "age_band_only", "model_average"}


def _rate_label(match_scope: Optional[str]) -> str:
    if match_scope in _VEHICLE_COMPARISON_SCOPES:
        return "Comparable-vehicle MOT failure rate"
    if match_scope in {"population_default", "unavailable"}:
        return "Dataset-wide reference MOT failure rate"
    return "Saved reference MOT failure rate"


def _vehicle_label(year: Optional[int], make: str, model: str) -> str:
    return " ".join(part for part in (str(year) if year is not None else "", make, model) if part)


def _build_risk_breakdown(risk_percentages: Dict[str, float], top_risks: List[str]) -> str:
    """Build comparable-vehicle component evidence HTML with progress bars."""
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

    # Use Jinja2 template for each component row (component names are internal, not user input)
    row_template = _jinja_env.from_string("""
        <tr>
            <td style="padding: 8px 0; width: 140px; font-size: 14px; color: #374151;">{{ component }}</td>
            <td style="padding: 8px 0;">
                <div style="background-color: #E5E7EB; border-radius: 4px; height: 8px; width: 100%;">
                    <div style="background-color: {{ bar_color }}; border-radius: 4px; height: 8px; width: {{ bar_width }}%;"></div>
                </div>
            </td>
            <td style="padding: 8px 0; width: 50px; text-align: right; font-size: 14px; font-weight: 600; color: #374151;">{{ pct_display }}%</td>
        </tr>
    """)

    for component, pct in risk_components:
        if pct > 0:
            pct_display = int(pct * 100) if pct < 1 else int(pct)
            bar_width = min(pct_display, 100)
            bar_color = "#DC2626" if pct_display >= 30 else ("#D97706" if pct_display >= 15 else "#059669")
            risk_breakdown_html += row_template.render(
                component=component,
                bar_color=bar_color,
                bar_width=bar_width,
                pct_display=pct_display
            )

    # If no risk percentages, show top risks as list
    if not risk_breakdown_html:
        risk_row_template = _jinja_env.from_string("""
            <tr>
                <td colspan="3" style="padding: 6px 0; font-size: 14px; color: #374151;">&bull; {{ risk }}</td>
            </tr>
        """)
        for risk in top_risks[:5]:
            risk_breakdown_html += risk_row_template.render(risk=risk.title())

    return risk_breakdown_html


def generate_lead_email(
    garage_name: str,
    lead_name: Optional[str],
    lead_email: str,
    lead_phone: Optional[str],
    lead_postcode: str,
    distance_miles: float,
    vehicle_make: str,
    vehicle_model: str,
    vehicle_year: Optional[int],
    failure_risk: Optional[float],
    top_risks: List[str],
    assignment_id: str,
    match_scope: Optional[str] = None,
    risk_percentages: Optional[Dict[str, float]] = None,
    garages_count: int = 3,
    leads_remaining: int = 5,
    max_leads_per_month: int = 5,
) -> Dict[str, str]:
    """
    Generate lead notification email for a garage.

    All user-provided data (lead_name, lead_email, lead_phone, lead_postcode,
    vehicle_make, vehicle_model) is automatically HTML-escaped by Jinja2
    to prevent XSS attacks.

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
        top_risks: List of top risk areas
        assignment_id: UUID for outcome tracking
        risk_percentages: Dict mapping risk areas to percentages
        garages_count: Number of garages this lead was sent to
        leads_remaining: Free leads remaining this month
        max_leads_per_month: Maximum free leads per month

    Returns:
        Dict with 'subject', 'html', and 'text' keys
    """
    # Default risk percentages if not provided
    if risk_percentages is None:
        risk_percentages = {}

    # Subject line stays an enquiry, not a diagnosed repair job.
    subject_template = _jinja_env.from_string(
        "New garage enquiry near you: {{ vehicle_make }} {{ vehicle_model }}"
    )
    subject = subject_template.render(
        vehicle_make=vehicle_make,
        vehicle_model=vehicle_model,
    )

    # Build comparable-vehicle component evidence HTML
    risk_breakdown_html = _build_risk_breakdown(risk_percentages, top_risks)

    # Build customer name HTML (user input - will be escaped)
    customer_name_template = _jinja_env.from_string(
        "<p style='margin: 0 0 8px 0; font-size: 16px; font-weight: 600; color: #1E293B;'>{{ name }}</p>"
    )
    customer_name_html = customer_name_template.render(name=lead_name) if lead_name else ""

    # Build phone HTML (user input - will be escaped)
    phone_template = _jinja_env.from_string("""
        <tr>
            <td style="padding: 4px 0; font-size: 14px; color: #64748B; width: 30px;">&#128222;</td>
            <td style="padding: 4px 0; font-size: 14px; color: #1E293B;">{{ phone }}</td>
        </tr>
    """)
    phone_html = phone_template.render(phone=lead_phone) if lead_phone else ""

    # Main HTML template with all user data properly escaped
    html_template = _jinja_env.from_string("""<!DOCTYPE html>
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
                <p style="margin: 8px 0 0 0; color: #94A3B8; font-size: 14px; letter-spacing: 0.5px;">MOT Record &amp; Comparable-Vehicle Evidence</p>
            </td>
        </tr>

        <!-- Free Lead Banner -->
        <tr>
            <td style="padding: 12px 24px; background-color: #DBEAFE; text-align: center;">
                <p style="margin: 0; font-size: 14px; color: #1E40AF; font-weight: 600;">
                    &#127873; FREE LEAD ({{ leads_remaining }} of {{ max_leads_per_month }} remaining this month)
                </p>
            </td>
        </tr>

        <!-- Main Content -->
        <tr>
            <td style="padding: 24px;">

                <!-- Vehicle -->
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 20px;">
                    <tr>
                        <td>
                            <p style="margin: 0 0 4px 0; font-size: 12px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">New Lead</p>
                            <h2 style="margin: 0 0 8px 0; font-size: 24px; color: #1E293B; font-weight: 700;">
                                {{ vehicle_label }}
                            </h2>
                        </td>
                    </tr>
                </table>

                <!-- Distance -->
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 24px;">
                    <tr>
                        <td style="padding: 16px; background-color: #F8FAFC; border-radius: 8px; border: 1px solid #E2E8F0;">
                            <p style="margin: 0; font-size: 12px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">Distance</p>
                            <p style="margin: 4px 0 0 0; font-size: 28px; font-weight: 700; color: #1E293B;">{{ distance_miles }} mi</p>
                        </td>
                    </tr>
                </table>

                {% if risk_breakdown_html or failure_rate is not none %}
                <!-- Comparison evidence -->
                <div style="margin-bottom: 24px;">
                    <h3 style="margin: 0 0 12px 0; font-size: 14px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">
                        Comparable-Vehicle Component Patterns
                    </h3>
                    {% if risk_breakdown_html %}<table width="100%" cellpadding="0" cellspacing="0">
                        {{ risk_breakdown_html|safe }}
                    </table>{% endif %}
                    {% if failure_rate is not none %}
                    <p style="margin: 12px 0 0 0; font-size: 13px; color: #64748B;">
                        {{ rate_label }}: <strong style="color: #1E293B;">{{ failure_rate }}%</strong>.
                        This is comparison evidence, not a diagnosis of the customer's car.
                    </p>
                    {% endif %}
                </div>
                {% endif %}

                <!-- Why This Lead -->
                <div style="margin-bottom: 24px; padding: 16px; background-color: #FEF3C7; border-radius: 8px; border-left: 4px solid #F59E0B;">
                    <h3 style="margin: 0 0 8px 0; font-size: 14px; color: #92400E; text-transform: uppercase; letter-spacing: 0.5px;">
                        Why This Lead?
                    </h3>
                    <p style="margin: 0; font-size: 14px; color: #78350F;">
                        You're <strong>{{ distance_miles }} miles</strong> from this customer. This lead was sent to <strong>{{ garages_count }} garages</strong> in your area.
                    </p>
                </div>

                <!-- Customer Contact -->
                <div style="margin-bottom: 24px; padding: 16px; background-color: #F8FAFC; border-radius: 8px;">
                    <h3 style="margin: 0 0 12px 0; font-size: 14px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">
                        Customer Contact
                    </h3>
                    {{ customer_name_html|safe }}
                    <table width="100%" cellpadding="0" cellspacing="0">
                        <tr>
                            <td style="padding: 4px 0; font-size: 14px; color: #64748B; width: 30px;">&#128231;</td>
                            <td style="padding: 4px 0; font-size: 14px; color: #1E293B;">{{ lead_email }}</td>
                        </tr>
                        {{ phone_html|safe }}
                        <tr>
                            <td style="padding: 4px 0; font-size: 14px; color: #64748B; width: 30px;">&#128205;</td>
                            <td style="padding: 4px 0; font-size: 14px; color: #1E293B;">{{ lead_postcode }}</td>
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
                            <a href="{{ base_url }}/api/garage/outcome/{{ assignment_id }}?result=won"
                               style="display: inline-block; padding: 10px 20px; background-color: #059669; color: #FFFFFF; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">
                                Won Job
                            </a>
                        </td>
                        <td style="padding-right: 8px;">
                            <a href="{{ base_url }}/api/garage/outcome/{{ assignment_id }}?result=lost"
                               style="display: inline-block; padding: 10px 20px; background-color: #E5E7EB; color: #374151; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">
                                Lost
                            </a>
                        </td>
                        <td>
                            <a href="{{ base_url }}/api/garage/outcome/{{ assignment_id }}?result=no_response"
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
                    The comparison rates draw on <strong>millions of recorded MOT results</strong> from the official DVSA dataset. They do not establish this vehicle's condition.
                </p>
            </td>
        </tr>

        <!-- Footer -->
        <tr>
            <td style="padding: 24px; background-color: #F8FAFC; border-top: 1px solid #E5E7EB;">
                <p style="margin: 0 0 8px 0; font-size: 13px; color: #64748B;">
                    You're on the <strong>Free plan</strong> ({{ max_leads_per_month }} leads/month)
                </p>
                <p style="margin: 0 0 16px 0; font-size: 13px;">
                    <a href="{{ base_url }}/pricing" style="color: #2563EB; text-decoration: none; font-weight: 600;">
                        Want unlimited leads? See our plans &rarr;
                    </a>
                </p>
                <p style="margin: 0 0 8px 0; font-size: 12px; color: #94A3B8;">
                    Questions? Reply to this email
                </p>
                <p style="margin: 0; font-size: 12px; color: #94A3B8;">
                    <a href="{{ base_url }}/dashboard" style="color: #64748B; text-decoration: none;">Dashboard</a> &middot;
                    <a href="{{ base_url }}/settings" style="color: #64748B; text-decoration: none;">Settings</a> &middot;
                    <a href="{{ base_url }}/support" style="color: #64748B; text-decoration: none;">Support</a>
                </p>
            </td>
        </tr>

    </table>
</body>
</html>""")

    html_body = html_template.render(
        leads_remaining=leads_remaining,
        max_leads_per_month=max_leads_per_month,
        vehicle_label=_vehicle_label(vehicle_year, vehicle_make, vehicle_model),
        distance_miles=distance_miles,
        risk_breakdown_html=risk_breakdown_html,
        failure_rate=round(failure_risk * 100) if failure_risk is not None else None,
        rate_label=_rate_label(match_scope),
        garages_count=garages_count,
        customer_name_html=customer_name_html,
        lead_email=lead_email,
        phone_html=phone_html,
        lead_postcode=lead_postcode,
        base_url=BASE_URL,
        assignment_id=assignment_id,
    )

    # Plain text version - also use Jinja2 for consistency
    text_template = _jinja_env.from_string("""
======================================================================
  AUTOSAFE - MOT Record & Comparable-Vehicle Evidence
======================================================================

FREE LEAD ({{ leads_remaining }} of {{ max_leads_per_month }} remaining this month)

----------------------------------------------------------------------

NEW LEAD: {{ vehicle_label }}

DISTANCE: {{ distance_miles }} miles from your garage
{% if failure_rate is not none %}{{ rate_label|upper }}: {{ failure_rate }}%
Comparison evidence only; not a diagnosis of this car.{% endif %}

COMPARABLE-VEHICLE COMPONENT PATTERNS:
{{ risk_list }}

----------------------------------------------------------------------

WHY THIS LEAD?
You're {{ distance_miles }} miles from this customer.
This lead was sent to {{ garages_count }} garages in your area.

----------------------------------------------------------------------

CUSTOMER CONTACT:
{% if lead_name %}Name: {{ lead_name }}
{% endif %}Email: {{ lead_email }}
{% if lead_phone %}Phone: {{ lead_phone }}
{% endif %}Location: {{ lead_postcode }}

----------------------------------------------------------------------

REPORT OUTCOME:
* Won Job: {{ base_url }}/api/garage/outcome/{{ assignment_id }}?result=won
* Lost: {{ base_url }}/api/garage/outcome/{{ assignment_id }}?result=lost
* Couldn't Reach: {{ base_url }}/api/garage/outcome/{{ assignment_id }}?result=no_response

----------------------------------------------------------------------

HOW WE KNOW THIS:
Our comparisons draw on real MOT results from the official DVSA dataset.

----------------------------------------------------------------------

You're on the Free plan ({{ max_leads_per_month }} leads/month)
Want unlimited leads? Visit {{ base_url }}/pricing

Questions? Reply to this email
""")

    risk_list = "\n".join(f"  * {risk.title()}" for risk in top_risks[:5]) if top_risks else "  No component comparison supplied"

    text_body = text_template.render(
        leads_remaining=leads_remaining,
        max_leads_per_month=max_leads_per_month,
        vehicle_label=_vehicle_label(vehicle_year, vehicle_make, vehicle_model),
        distance_miles=distance_miles,
        failure_rate=round(failure_risk * 100) if failure_risk is not None else None,
        rate_label=_rate_label(match_scope),
        risk_list=risk_list,
        garages_count=garages_count,
        lead_name=lead_name,
        lead_email=lead_email,
        lead_phone=lead_phone,
        lead_postcode=lead_postcode,
        base_url=BASE_URL,
        assignment_id=assignment_id,
    )

    return {
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }


def generate_mot_reminder_confirmation(
    email: str,
    registration: str,
    vehicle_make: str,
    vehicle_model: str,
    vehicle_year: Optional[int],
    mot_expiry_date: Optional[str] = None,
    failure_risk: Optional[float] = None,
    match_scope: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate MOT reminder confirmation email.

    Args:
        email: Subscriber email
        registration: Vehicle registration
        vehicle_make/model/year: Vehicle details
        mot_expiry_date: ISO date string
        failure_risk: Overall failure risk (0-1)

    Returns:
        Dict with 'subject', 'html', and 'text' keys
    """
    # Format MOT date for display
    mot_display = "Unknown"
    if mot_expiry_date:
        try:
            from datetime import datetime as dt
            d = dt.strptime(mot_expiry_date[:10], "%Y-%m-%d")
            mot_display = d.strftime("%d %B %Y")
        except (ValueError, IndexError):
            mot_display = mot_expiry_date[:10]

    # Optional saved comparison-rate display.
    risk_display = ""
    risk_text = ""
    if failure_risk is not None:
        risk_pct = int(failure_risk * 100)
        risk_level, risk_color = _get_risk_level(failure_risk)
        risk_display_template = _jinja_env.from_string("""
            <div style="margin: 16px 0; padding: 12px 16px; background-color: #F8FAFC; border-radius: 8px; border-left: 4px solid {{ color }};">
                <p style="margin: 0; font-size: 14px; color: #374151;">
                    {{ rate_label }}: <strong style="color: {{ color }};">{{ pct }}% ({{ level }})</strong><br>
                    <span style="font-size: 12px; color: #64748B;">A recorded group rate, not a diagnosis of this car.</span>
                </p>
            </div>
        """)
        label = _rate_label(match_scope)
        risk_display = risk_display_template.render(
            color=risk_color, pct=risk_pct, level=risk_level, rate_label=label
        )
        risk_text = (
            f"{label}: {risk_pct}% ({risk_level}). "
            "A recorded group rate, not a diagnosis of this car."
        )

    subject = f"MOT Reminder Set - {vehicle_make} {vehicle_model} ({registration})"

    html_template = _jinja_env.from_string("""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #F3F4F6;">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 560px; margin: 0 auto; background-color: #FFFFFF;">
        <tr>
            <td style="padding: 24px; background: linear-gradient(135deg, #1E293B 0%, #334155 100%); text-align: center;">
                <h1 style="margin: 0; color: #FFFFFF; font-size: 24px; font-weight: 700; font-family: Georgia, serif;">AutoSafe</h1>
            </td>
        </tr>
        <tr>
            <td style="padding: 32px 24px;">
                <h2 style="margin: 0 0 8px 0; font-size: 20px; color: #1E293B; font-weight: 700;">
                    Your MOT reminder is set
                </h2>
                <p style="margin: 0 0 20px 0; font-size: 15px; color: #64748B;">
                    We'll email you 4 weeks before your MOT is due.
                </p>

                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 20px; background-color: #F8FAFC; border-radius: 8px;">
                    <tr>
                        <td style="padding: 16px;">
                            <p style="margin: 0 0 4px 0; font-size: 12px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">Vehicle</p>
                            <p style="margin: 0 0 12px 0; font-size: 16px; color: #1E293B; font-weight: 600;">
                                {{ vehicle_label }} ({{ registration }})
                            </p>
                            <p style="margin: 0 0 4px 0; font-size: 12px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">MOT Due</p>
                            <p style="margin: 0; font-size: 16px; color: #1E293B; font-weight: 600;">{{ mot_display }}</p>
                        </td>
                    </tr>
                </table>

                {{ risk_display|safe }}

                <div style="margin: 24px 0; padding: 16px; background-color: #F0FDF4; border-radius: 8px; text-align: center;">
                    <p style="margin: 0 0 8px 0; font-size: 14px; color: #166534; font-weight: 600;">Need a garage?</p>
                    <p style="margin: 0; font-size: 14px; color: #15803D;">
                        Simply reply to this email and we'll find one near you.
                    </p>
                </div>

                <p style="margin: 24px 0 0 0; font-size: 12px; color: #94A3B8; text-align: center;">
                    You can unsubscribe at any time by replying to this email.
                </p>
            </td>
        </tr>
        <tr>
            <td style="padding: 16px 24px; background-color: #F8FAFC; border-top: 1px solid #E5E7EB; text-align: center;">
                <p style="margin: 0; font-size: 12px; color: #94A3B8;">
                    AutoSafe &middot; MOT Record &amp; Comparable-Vehicle Evidence &middot;
                    <a href="{{ base_url }}/privacy" style="color: #64748B; text-decoration: none;">Privacy</a>
                </p>
            </td>
        </tr>
    </table>
</body>
</html>""")

    html_body = html_template.render(
        vehicle_label=_vehicle_label(vehicle_year, vehicle_make, vehicle_model),
        registration=registration,
        mot_display=mot_display,
        risk_display=risk_display,
        base_url=BASE_URL,
    )

    text_template = _jinja_env.from_string("""
MOT REMINDER SET - AutoSafe
============================

Your MOT reminder is confirmed.

Vehicle: {{ vehicle_label }} ({{ registration }})
MOT Due: {{ mot_display }}
{% if risk_text %}{{ risk_text }}{% endif %}

We'll email you 4 weeks before your MOT is due.

Need a garage? Reply to this email and we'll find one near you.

Unsubscribe any time by replying to this email.

AutoSafe - {{ base_url }}
""")

    text_body = text_template.render(
        vehicle_label=_vehicle_label(vehicle_year, vehicle_make, vehicle_model),
        registration=registration,
        mot_display=mot_display,
        risk_text=risk_text,
        base_url=BASE_URL,
    )

    return {
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }


def generate_report_email(
    email: str,
    registration: str,
    vehicle_make: str,
    vehicle_model: str,
    vehicle_year: Optional[int],
    failure_risk: float,
    match_scope: Optional[str],
    common_faults: List[Dict],
    repair_cost_min: Optional[int] = None,
    repair_cost_max: Optional[int] = None,
    mot_expiry_date: Optional[str] = None,
    days_until_mot_expiry: Optional[int] = None,
) -> Dict[str, str]:
    """
    Generate a designed report email for the user.

    Returns:
        Dict with 'subject', 'html', and 'text' keys
    """
    failure_rate_percent = round(failure_risk * 100)

    # MOT expiry display
    mot_display = ""
    if mot_expiry_date:
        try:
            from datetime import datetime as dt
            d = dt.strptime(mot_expiry_date[:10], "%Y-%m-%d")
            mot_display = d.strftime("%d %B %Y")
        except (ValueError, IndexError):
            mot_display = mot_expiry_date[:10]

    # Build fault rows
    fault_rows = ""
    fault_row_template = _jinja_env.from_string("""
        <tr>
            <td style="padding: 8px 0; font-size: 14px; color: #374151;">{{ component }}</td>
            <td style="padding: 8px 0; text-align: right;">
                <span style="display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;
                    background-color: {{ bg_color }}; color: {{ text_color }};">
                    {{ level }}
                </span>
            </td>
        </tr>
    """)
    for fault in common_faults[:6]:
        level = fault.get('risk_level', 'Low')
        if level == 'High':
            bg_color, text_color = "#FEE2E2", "#DC2626"
        elif level == 'Medium':
            bg_color, text_color = "#FEF3C7", "#D97706"
        else:
            bg_color, text_color = "#DBEAFE", "#2563EB"
        fault_rows += fault_row_template.render(
            component=fault.get('component', 'Unknown'),
            level=level,
            bg_color=bg_color,
            text_color=text_color,
        )

    # Repair cost display
    cost_display = ""
    if repair_cost_min is not None and repair_cost_max is not None:
        cost_display = f"£{repair_cost_min} - £{repair_cost_max}"

    subject_template = _jinja_env.from_string(
        "Your AutoSafe Report - {{ make }} {{ model }} ({{ reg }})"
    )
    subject = subject_template.render(make=vehicle_make, model=vehicle_model, reg=registration)

    html_template = _jinja_env.from_string("""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #F3F4F6;">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 560px; margin: 0 auto; background-color: #FFFFFF;">
        <tr>
            <td style="padding: 24px; background: linear-gradient(135deg, #1E293B 0%, #334155 100%); text-align: center;">
                <h1 style="margin: 0; color: #FFFFFF; font-size: 24px; font-weight: 700; font-family: Georgia, serif;">AutoSafe</h1>
                <p style="margin: 6px 0 0 0; color: #94A3B8; font-size: 13px;">Vehicle Record &amp; Comparison Report</p>
            </td>
        </tr>
        <tr>
            <td style="padding: 24px;">
                <h2 style="margin: 0 0 4px 0; font-size: 20px; color: #1E293B;">
                    {{ vehicle_label }}
                </h2>
                <p style="margin: 0 0 24px 0; font-size: 14px; color: #64748B;">Registration: {{ registration }}</p>

                <!-- One evidence-backed number; no renamed complement. -->
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 24px;">
                    <tr>
                        <td style="padding: 16px; background-color: #F8FAFC; border-radius: 8px; text-align: center; border: 1px solid #E2E8F0;">
                            <p style="margin: 0 0 4px 0; font-size: 11px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">{{ rate_label }}</p>
                            <p style="margin: 0; font-size: 32px; font-weight: 700; color: #1E293B;">{{ failure_rate }}%</p>
                            <p style="margin: 6px 0 0 0; font-size: 12px; color: #64748B;">A recorded group rate, not a diagnosis or next-MOT result for this car.</p>
                        </td>
                    </tr>
                </table>

                {% if cost_display %}
                <div style="margin-bottom: 24px; padding: 12px 16px; background-color: #FFF7ED; border-radius: 8px; border: 1px solid #FED7AA; text-align: center;">
                    <p style="margin: 0 0 2px 0; font-size: 11px; color: #9A3412; text-transform: uppercase;">Indicative Comparison-Based Repair Range</p>
                    <p style="margin: 0; font-size: 20px; font-weight: 700; color: #9A3412;">{{ cost_display }}</p>
                    <p style="margin: 4px 0 0 0; font-size: 11px; color: #9A3412;">Only if related work is needed; not a quote or diagnosis.</p>
                </div>
                {% endif %}

                {% if mot_display %}
                <div style="margin-bottom: 24px; padding: 12px 16px; background-color: #F0F9FF; border-radius: 8px; border: 1px solid #BAE6FD;">
                    <table width="100%" cellpadding="0" cellspacing="0">
                        <tr>
                            <td>
                                <p style="margin: 0 0 2px 0; font-size: 11px; color: #0369A1; text-transform: uppercase;">MOT Expiry</p>
                                <p style="margin: 0; font-size: 16px; font-weight: 600; color: #0C4A6E;">{{ mot_display }}</p>
                            </td>
                            {% if days_until is not none %}
                            <td style="text-align: right;">
                                <p style="margin: 0; font-size: 24px; font-weight: 700; color: #0369A1;">{{ days_until }}d</p>
                            </td>
                            {% endif %}
                        </tr>
                    </table>
                </div>
                {% endif %}

                {% if fault_rows %}
                <h3 style="margin: 0 0 12px 0; font-size: 14px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">
                    Comparable-Vehicle Component Patterns (Not Diagnosed Faults)
                </h3>
                <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 24px;">
                    {{ fault_rows|safe }}
                </table>
                {% endif %}

                <div style="margin: 24px 0; padding: 16px; background-color: #F0FDF4; border-radius: 8px; text-align: center;">
                    <p style="margin: 0 0 8px 0; font-size: 14px; color: #166534; font-weight: 600;">Need a garage?</p>
                    <p style="margin: 0; font-size: 14px; color: #15803D;">
                        Reply to this email and we'll find one near you.
                    </p>
                </div>
            </td>
        </tr>
        <tr>
            <td style="padding: 16px 24px; background-color: #1E293B; text-align: center;">
                <p style="margin: 0; font-size: 12px; color: #94A3B8;">
                    Based on 148M+ recorded DVSA MOT tests
                </p>
            </td>
        </tr>
        <tr>
            <td style="padding: 16px 24px; background-color: #F8FAFC; border-top: 1px solid #E5E7EB; text-align: center;">
                <p style="margin: 0; font-size: 12px; color: #94A3B8;">
                    AutoSafe &middot;
                    <a href="{{ base_url }}/privacy" style="color: #64748B; text-decoration: none;">Privacy</a> &middot;
                    <a href="{{ base_url }}" style="color: #64748B; text-decoration: none;">Check another vehicle</a>
                </p>
            </td>
        </tr>
    </table>
</body>
</html>""")

    html_body = html_template.render(
        vehicle_label=_vehicle_label(vehicle_year, vehicle_make, vehicle_model),
        registration=registration,
        failure_rate=failure_rate_percent,
        rate_label=_rate_label(match_scope),
        cost_display=cost_display,
        mot_display=mot_display,
        days_until=days_until_mot_expiry,
        fault_rows=fault_rows,
        base_url=BASE_URL,
    )

    # Plain text version
    fault_text = ""
    for fault in common_faults[:6]:
        fault_text += f"  - {fault.get('component', 'Unknown')}: {fault.get('risk_level', 'Unknown')} recorded rate\n"

    text_template = _jinja_env.from_string("""
AUTOSAFE VEHICLE REPORT
========================

{{ vehicle_label }} ({{ registration }})

{{ rate_label|upper }}: {{ failure_rate }}%
This is a recorded group rate, not a diagnosis or next-MOT result for this car.
{% if cost_display %}INDICATIVE COMPARISON-BASED REPAIR RANGE: {{ cost_display }} (not a quote or diagnosis){% endif %}
{% if mot_display %}MOT EXPIRY: {{ mot_display }}{% endif %}

COMPARABLE-VEHICLE COMPONENT PATTERNS:
{{ fault_text }}
Need a garage? Reply to this email and we'll find one near you.

Based on 148M+ recorded DVSA MOT tests.

AutoSafe - {{ base_url }}
""")

    text_body = text_template.render(
        vehicle_label=_vehicle_label(vehicle_year, vehicle_make, vehicle_model),
        registration=registration,
        failure_rate=failure_rate_percent,
        rate_label=_rate_label(match_scope),
        cost_display=cost_display,
        mot_display=mot_display,
        fault_text=fault_text,
        base_url=BASE_URL,
    )

    return {
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }


def generate_mot_reminder_28d(
    email: str,
    registration: str,
    vehicle_make: str,
    vehicle_model: str,
    vehicle_year: Optional[int],
    mot_expiry_date: str,
    failure_risk: Optional[float] = None,
    match_scope: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate the 28-day MOT reminder email.

    This is the actual reminder sent ~4 weeks before the MOT is due,
    fulfilling the promise made in the confirmation email.

    Returns:
        Dict with 'subject', 'html', and 'text' keys
    """
    # Format MOT date
    mot_display = mot_expiry_date[:10]
    try:
        from datetime import datetime as dt
        d = dt.strptime(mot_expiry_date[:10], "%Y-%m-%d")
        mot_display = d.strftime("%d %B %Y")
    except (ValueError, IndexError):
        pass

    # Risk section
    risk_html = ""
    risk_text = ""
    if failure_risk is not None:
        risk_pct = int(failure_risk * 100)
        risk_level, risk_color = _get_risk_level(failure_risk)
        risk_template = _jinja_env.from_string("""
            <div style="margin: 20px 0; padding: 16px; background-color: #FEF2F2; border-radius: 8px; border-left: 4px solid {{ color }};">
                <p style="margin: 0 0 4px 0; font-size: 12px; color: #64748B; text-transform: uppercase; letter-spacing: 0.5px;">{{ rate_label }}</p>
                <p style="margin: 0; font-size: 24px; font-weight: 700; color: {{ color }};">{{ pct }}%</p>
                <p style="margin: 4px 0 0 0; font-size: 13px; color: #64748B;">
                    This recorded group rate does not diagnose your car. A physical check is needed to identify faults.
                </p>
            </div>
        """)
        label = _rate_label(match_scope)
        risk_html = risk_template.render(color=risk_color, pct=risk_pct, rate_label=label)
        risk_text = f"{label}: {risk_pct}% ({risk_level}); not a diagnosis of this car."

    subject_template = _jinja_env.from_string(
        "Your MOT is due in 4 weeks - {{ make }} {{ model }} ({{ reg }})"
    )
    subject = subject_template.render(make=vehicle_make, model=vehicle_model, reg=registration)

    html_template = _jinja_env.from_string("""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #F3F4F6;">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 560px; margin: 0 auto; background-color: #FFFFFF;">
        <tr>
            <td style="padding: 24px; background: linear-gradient(135deg, #1E293B 0%, #334155 100%); text-align: center;">
                <h1 style="margin: 0; color: #FFFFFF; font-size: 24px; font-weight: 700; font-family: Georgia, serif;">AutoSafe</h1>
            </td>
        </tr>

        <tr>
            <td style="padding: 12px 24px; background-color: #FEF3C7; text-align: center;">
                <p style="margin: 0; font-size: 14px; color: #92400E; font-weight: 600;">
                    &#9200; Your MOT is due in approximately 4 weeks
                </p>
            </td>
        </tr>

        <tr>
            <td style="padding: 32px 24px;">
                <h2 style="margin: 0 0 8px 0; font-size: 20px; color: #1E293B; font-weight: 700;">
                    Time to prepare for your MOT
                </h2>
                <p style="margin: 0 0 20px 0; font-size: 15px; color: #64748B;">
                    Your {{ vehicle_label }} ({{ registration }}) has its MOT due on <strong>{{ mot_display }}</strong>.
                </p>

                {{ risk_html|safe }}

                <table width="100%" cellpadding="0" cellspacing="0" style="margin: 24px 0;">
                    <tr>
                        <td style="padding: 16px; background-color: #F8FAFC; border-radius: 8px;">
                            <p style="margin: 0 0 12px 0; font-size: 14px; color: #1E293B; font-weight: 600;">Quick pre-MOT checklist:</p>
                            <table cellpadding="0" cellspacing="0">
                                <tr><td style="padding: 3px 0; font-size: 14px; color: #475569;">&#9744; Check all lights and indicators</td></tr>
                                <tr><td style="padding: 3px 0; font-size: 14px; color: #475569;">&#9744; Check tyre tread depth (min 1.6mm)</td></tr>
                                <tr><td style="padding: 3px 0; font-size: 14px; color: #475569;">&#9744; Top up washer fluid</td></tr>
                                <tr><td style="padding: 3px 0; font-size: 14px; color: #475569;">&#9744; Check windscreen for chips/cracks</td></tr>
                                <tr><td style="padding: 3px 0; font-size: 14px; color: #475569;">&#9744; Test horn and wipers</td></tr>
                            </table>
                        </td>
                    </tr>
                </table>

                <div style="margin: 24px 0; text-align: center;">
                    <a href="{{ base_url }}?utm_source=reminder&utm_medium=email&utm_campaign=mot_28d"
                       style="display: inline-block; padding: 14px 32px; background-color: #1E293B; color: #FFFFFF; text-decoration: none; border-radius: 8px; font-size: 15px; font-weight: 600;">
                        Refresh your comparison report
                    </a>
                </div>

                <div style="margin: 24px 0; padding: 16px; background-color: #F0FDF4; border-radius: 8px; text-align: center;">
                    <p style="margin: 0 0 8px 0; font-size: 14px; color: #166534; font-weight: 600;">Need a garage for your MOT?</p>
                    <p style="margin: 0; font-size: 14px; color: #15803D;">
                        Reply to this email and we'll find one near you.
                    </p>
                </div>

                <p style="margin: 24px 0 0 0; font-size: 12px; color: #94A3B8; text-align: center;">
                    You can unsubscribe at any time by replying to this email.
                </p>
            </td>
        </tr>
        <tr>
            <td style="padding: 16px 24px; background-color: #F8FAFC; border-top: 1px solid #E5E7EB; text-align: center;">
                <p style="margin: 0; font-size: 12px; color: #94A3B8;">
                    AutoSafe &middot; MOT Record &amp; Comparable-Vehicle Evidence &middot;
                    <a href="{{ base_url }}/privacy" style="color: #64748B; text-decoration: none;">Privacy</a>
                </p>
            </td>
        </tr>
    </table>
</body>
</html>""")

    html_body = html_template.render(
        vehicle_label=_vehicle_label(vehicle_year, vehicle_make, vehicle_model),
        registration=registration,
        mot_display=mot_display,
        risk_html=risk_html,
        base_url=BASE_URL,
    )

    text_template = _jinja_env.from_string("""
YOUR MOT IS DUE IN 4 WEEKS - AutoSafe
=======================================

Your {{ vehicle_label }} ({{ registration }})
has its MOT due on {{ mot_display }}.

{% if risk_text %}{{ risk_text }}{% endif %}

QUICK PRE-MOT CHECKLIST:
[ ] Check all lights and indicators
[ ] Check tyre tread depth (min 1.6mm)
[ ] Top up washer fluid
[ ] Check windscreen for chips/cracks
[ ] Test horn and wipers

Refresh your comparison report: {{ base_url }}?utm_source=reminder&utm_medium=email&utm_campaign=mot_28d

Need a garage? Reply to this email and we'll find one near you.

Unsubscribe any time by replying to this email.

AutoSafe - {{ base_url }}
""")

    text_body = text_template.render(
        vehicle_label=_vehicle_label(vehicle_year, vehicle_make, vehicle_model),
        registration=registration,
        mot_display=mot_display,
        risk_text=risk_text,
        base_url=BASE_URL,
    )

    return {
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
