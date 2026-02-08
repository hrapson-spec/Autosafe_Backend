"""
Story Templates for Data Stories
==================================

Jinja2 templates that render story data into:
  - Markdown (for press pitches / email)
  - HTML (for /insights page)
"""

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

# Reuse the same template directory as seo_pages.py
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)


def render_markdown(story: dict) -> str:
    """Render a story as markdown for press pitches."""
    story_type = story["story_type"]

    if story_type in ("reliability_ranking", "most_reliable", "first_mot_failures"):
        return _ranking_markdown(story)
    elif story_type == "component_breakdown":
        return _component_markdown(story)
    else:
        raise ValueError(f"Unknown story type: {story_type}")


def render_html(story: dict) -> str:
    """Render a story as HTML using seo_insights_story.html template."""
    template = jinja_env.get_template("seo_insights_story.html")
    return template.render(
        story=story,
        today=date.today().isoformat(),
    )


def render_pitch_email(story: dict) -> str:
    """Render a media pitch email template."""
    return f"""Subject: Data story: {story['title']}

Hi [Name],

I thought this might be of interest for [publication]:

{story['key_stat']}.

This is from AutoSafe's analysis of 142 million official DVSA MOT test records.

Key findings:
{_bullet_points(story)}

Full methodology and data tables: https://www.autosafe.one/insights/{story['slug']}/

We can provide:
- High-resolution charts (attached or linked)
- Additional data cuts (by region, by age band, by component)
- Expert commentary on the findings

The data is sourced from official UK government MOT records under the Open Government Licence v3.0.

Best regards,
AutoSafe Team
autosafehq@gmail.com
https://www.autosafe.one
"""


def _bullet_points(story: dict) -> str:
    """Generate key bullet points for press pitch."""
    story_type = story["story_type"]

    if story_type in ("reliability_ranking", "most_reliable", "first_mot_failures"):
        data = story.get("data", [])
        lines = []
        for item in data[:5]:
            lines.append(
                f"- {item['make']} {item['model']}: {item['fail_rate']}% failure rate "
                f"({item['total_tests']:,} tests)"
            )
        return "\n".join(lines)
    elif story_type == "component_breakdown":
        risks = story.get("overall_risks", [])
        lines = []
        for r in risks:
            lines.append(f"- {r['component']}: {r['risk']}% failure risk")
        return "\n".join(lines)
    return ""


def _ranking_markdown(story: dict) -> str:
    """Markdown for ranking-type stories."""
    lines = [
        f"# {story['title']}",
        "",
        f"*{story['subtitle']}*",
        "",
        f"**Key finding:** {story['key_stat']}",
        "",
        "| Rank | Vehicle | Failure Rate | Tests | Worst Area |",
        "|------|---------|-------------|-------|------------|",
    ]
    for item in story["data"]:
        lines.append(
            f"| {item['rank']} | {item['make']} {item['model']} | "
            f"{item['fail_rate']}% | {item['total_tests']:,} | "
            f"{item['worst_component']} |"
        )
    lines.extend([
        "",
        f"**Methodology:** {story['methodology']}",
        "",
        f"*Source: AutoSafe analysis of DVSA MOT data. Published {date.today().isoformat()}.*",
        "",
        "Full report: https://www.autosafe.one/insights/" + story["slug"] + "/",
    ])
    return "\n".join(lines)


def _component_markdown(story: dict) -> str:
    """Markdown for component breakdown story."""
    lines = [
        f"# {story['title']}",
        "",
        f"*{story['subtitle']}*",
        "",
        f"**Key finding:** {story['key_stat']}",
        "",
        "## Overall Component Failure Rates",
        "",
        "| Component | Failure Risk |",
        "|-----------|-------------|",
    ]
    for r in story["overall_risks"]:
        lines.append(f"| {r['component']} | {r['risk']}% |")

    if story.get("age_bands"):
        lines.extend([
            "",
            "## How Component Risks Change With Age",
            "",
        ])
        # Header
        components = [r["component"] for r in story["overall_risks"]]
        header = "| Age Band | " + " | ".join(components) + " |"
        separator = "|----------|" + "|".join(["-----" for _ in components]) + "|"
        lines.append(header)
        lines.append(separator)
        for ab in story["age_bands"]:
            vals = [f"{ab['components'].get(c, 0)}%" for c in components]
            lines.append(f"| {ab['age_band']} yrs | " + " | ".join(vals) + " |")

    lines.extend([
        "",
        f"**Methodology:** {story['methodology']}",
        "",
        f"*Source: AutoSafe analysis of DVSA MOT data ({story['total_tests']:,} tests). "
        f"Published {date.today().isoformat()}.*",
        "",
        "Full report: https://www.autosafe.one/insights/" + story["slug"] + "/",
    ])
    return "\n".join(lines)
