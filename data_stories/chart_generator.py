"""
Chart Generator for Data Stories
==================================

Generates PNG bar/line charts in AutoSafe brand colours using matplotlib.
"""

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

logger = logging.getLogger(__name__)

# AutoSafe brand colours (from seo_base.html)
BRAND_GOLD = "#e5c07b"
BRAND_RED = "#ef4444"
BRAND_GREEN = "#22c55e"
BRAND_BG = "#1a1a1a"
BRAND_CARD_BG = "#2a2a2a"
BRAND_TEXT = "#a0a0a0"
BRAND_WHITE = "#ffffff"


def _apply_brand_style(fig, ax):
    """Apply AutoSafe dark theme to a matplotlib figure."""
    fig.patch.set_facecolor(BRAND_BG)
    ax.set_facecolor(BRAND_BG)
    ax.tick_params(colors=BRAND_TEXT, labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#333333")
    ax.spines["bottom"].set_color("#333333")
    ax.xaxis.label.set_color(BRAND_TEXT)
    ax.yaxis.label.set_color(BRAND_TEXT)


def _bar_color(value: float, threshold_high: float = 35, threshold_low: float = 20) -> str:
    """Pick bar colour based on value."""
    if value >= threshold_high:
        return BRAND_RED
    elif value <= threshold_low:
        return BRAND_GREEN
    return BRAND_GOLD


def generate_ranking_chart(story: dict, output_path: Path) -> Path:
    """
    Horizontal bar chart for reliability rankings.

    story: output from query_reliability_ranking()
    """
    data = story["data"]
    if not data:
        logger.warning("No data for ranking chart")
        return None

    labels = [f"{d['make']} {d['model']}" for d in reversed(data)]
    values = [d["fail_rate"] for d in reversed(data)]
    colors = [_bar_color(v) for v in values]

    fig, ax = plt.subplots(figsize=(10, max(6, len(data) * 0.6)))
    _apply_brand_style(fig, ax)

    bars = ax.barh(labels, values, color=colors, height=0.6, edgecolor="none")

    # Add value labels on bars
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
            f"{val}%", va="center", ha="left",
            color=BRAND_WHITE, fontsize=10, fontweight="bold",
        )

    ax.set_xlabel("MOT Failure Rate (%)", fontsize=11)
    ax.set_title(story["title"], color=BRAND_WHITE, fontsize=14, fontweight="bold", pad=15)
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))

    # Add UK average line
    ax.axvline(x=28, color=BRAND_TEXT, linestyle="--", linewidth=1, alpha=0.7)
    ax.text(28.5, len(data) - 0.5, "UK avg (28%)", color=BRAND_TEXT, fontsize=9, va="top")

    # Watermark
    fig.text(0.99, 0.01, "autosafe.one", ha="right", va="bottom",
             color=BRAND_TEXT, fontsize=9, alpha=0.5)

    plt.tight_layout()
    chart_path = output_path / f"{story['slug']}.png"
    fig.savefig(str(chart_path), dpi=150, bbox_inches="tight", facecolor=BRAND_BG)
    plt.close(fig)

    logger.info(f"Chart saved: {chart_path}")
    return chart_path


def generate_component_chart(story: dict, output_path: Path) -> Path:
    """
    Horizontal bar chart for component breakdown.

    story: output from query_component_breakdown()
    """
    risks = story["overall_risks"]
    if not risks:
        logger.warning("No data for component chart")
        return None

    labels = [r["component"] for r in reversed(risks)]
    values = [r["risk"] for r in reversed(risks)]
    colors = [_bar_color(v, threshold_high=15, threshold_low=8) for v in values]

    fig, ax = plt.subplots(figsize=(10, 5))
    _apply_brand_style(fig, ax)

    bars = ax.barh(labels, values, color=colors, height=0.6, edgecolor="none")

    for bar, val in zip(bars, values):
        ax.text(
            bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
            f"{val}%", va="center", ha="left",
            color=BRAND_WHITE, fontsize=10, fontweight="bold",
        )

    ax.set_xlabel("Failure Risk (%)", fontsize=11)
    ax.set_title(story["title"], color=BRAND_WHITE, fontsize=14, fontweight="bold", pad=15)
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))

    fig.text(0.99, 0.01, "autosafe.one", ha="right", va="bottom",
             color=BRAND_TEXT, fontsize=9, alpha=0.5)

    plt.tight_layout()
    chart_path = output_path / f"{story['slug']}.png"
    fig.savefig(str(chart_path), dpi=150, bbox_inches="tight", facecolor=BRAND_BG)
    plt.close(fig)

    logger.info(f"Chart saved: {chart_path}")
    return chart_path


def generate_age_component_chart(story: dict, output_path: Path) -> Path:
    """
    Grouped bar chart: component risks by age band.

    story: output from query_component_breakdown()
    """
    age_bands = story.get("age_bands", [])
    if not age_bands:
        return None

    # Pick top 4 components for readability
    top_components = [r["component"] for r in story["overall_risks"][:4]]
    band_labels = [ab["age_band"] + " yrs" for ab in age_bands]

    fig, ax = plt.subplots(figsize=(10, 6))
    _apply_brand_style(fig, ax)

    import numpy as np
    x = np.arange(len(band_labels))
    width = 0.18
    component_colors = [BRAND_RED, BRAND_GOLD, BRAND_GREEN, "#60a5fa"]

    for i, comp in enumerate(top_components):
        vals = [ab["components"].get(comp, 0) for ab in age_bands]
        offset = (i - len(top_components) / 2 + 0.5) * width
        ax.bar(x + offset, vals, width, label=comp, color=component_colors[i], alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(band_labels, color=BRAND_TEXT)
    ax.set_ylabel("Failure Risk (%)", fontsize=11)
    ax.set_title("Component Failure Rates by Vehicle Age", color=BRAND_WHITE,
                 fontsize=14, fontweight="bold", pad=15)
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
    ax.legend(loc="upper left", fontsize=9, facecolor=BRAND_CARD_BG,
              edgecolor="#333", labelcolor=BRAND_TEXT)

    fig.text(0.99, 0.01, "autosafe.one", ha="right", va="bottom",
             color=BRAND_TEXT, fontsize=9, alpha=0.5)

    plt.tight_layout()
    chart_path = output_path / "component-by-age.png"
    fig.savefig(str(chart_path), dpi=150, bbox_inches="tight", facecolor=BRAND_BG)
    plt.close(fig)

    logger.info(f"Chart saved: {chart_path}")
    return chart_path


# Map story types to chart generators
CHART_GENERATORS = {
    "reliability_ranking": generate_ranking_chart,
    "most_reliable": generate_ranking_chart,
    "first_mot_failures": generate_ranking_chart,
    "component_breakdown": generate_component_chart,
}
