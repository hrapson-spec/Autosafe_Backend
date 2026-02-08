"""
CLI entry point for Data Stories generation.

Usage:
    python -m data_stories.generate --story reliability_ranking --output-dir /tmp/stories
    python -m data_stories.generate --story all --output-dir /tmp/stories
    python -m data_stories.generate --list

Available stories:
    reliability_ranking  - Britain's 10 Least Reliable Cars for MOT
    most_reliable        - Britain's 10 Most Reliable Cars for MOT
    first_mot_failures   - Cars Most Likely to Fail Their First MOT
    component_breakdown  - MOT Failure Breakdown by Component
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from .query_engine import STORY_QUERIES
from .chart_generator import CHART_GENERATORS, generate_age_component_chart
from .story_templates import render_markdown, render_html, render_pitch_email

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def generate_story(story_name: str, output_dir: Path) -> dict:
    """Generate all outputs for a single story."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Query data
    logger.info(f"Querying data for '{story_name}'...")
    query_fn = STORY_QUERIES[story_name]
    story = query_fn()

    slug = story["slug"]
    story_dir = output_dir / slug
    story_dir.mkdir(parents=True, exist_ok=True)

    # 2. Generate markdown
    md = render_markdown(story)
    md_path = story_dir / f"{slug}.md"
    md_path.write_text(md, encoding="utf-8")
    logger.info(f"  Markdown: {md_path}")

    # 3. Generate HTML
    html = render_html(story)
    html_path = story_dir / f"{slug}.html"
    html_path.write_text(html, encoding="utf-8")
    logger.info(f"  HTML: {html_path}")

    # 4. Generate chart
    chart_fn = CHART_GENERATORS.get(story_name)
    if chart_fn:
        chart_path = chart_fn(story, story_dir)
        if chart_path:
            logger.info(f"  Chart: {chart_path}")

    # 4b. Extra chart for component breakdown
    if story_name == "component_breakdown":
        age_chart = generate_age_component_chart(story, story_dir)
        if age_chart:
            logger.info(f"  Age chart: {age_chart}")

    # 5. Generate pitch email
    pitch = render_pitch_email(story)
    pitch_path = story_dir / f"{slug}-pitch.txt"
    pitch_path.write_text(pitch, encoding="utf-8")
    logger.info(f"  Pitch: {pitch_path}")

    # 6. Save raw data as JSON (for programmatic use)
    json_path = story_dir / f"{slug}.json"
    json_path.write_text(json.dumps(story, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"  JSON: {json_path}")

    return story


def main():
    parser = argparse.ArgumentParser(
        description="Generate AutoSafe data stories for press and /insights page",
    )
    parser.add_argument(
        "--story",
        choices=list(STORY_QUERIES.keys()) + ["all"],
        help="Which story to generate (or 'all')",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data_stories/output"),
        help="Directory for generated files (default: data_stories/output)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available story types",
    )

    args = parser.parse_args()

    if args.list:
        print("Available stories:")
        for name in STORY_QUERIES:
            print(f"  {name}")
        return

    if not args.story:
        parser.error("--story is required (or use --list)")

    stories_to_generate = list(STORY_QUERIES.keys()) if args.story == "all" else [args.story]

    for story_name in stories_to_generate:
        logger.info(f"=== Generating: {story_name} ===")
        try:
            story = generate_story(story_name, args.output_dir)
            print(f"\n  Key stat: {story['key_stat']}")
        except Exception as e:
            logger.error(f"Failed to generate '{story_name}': {e}")
            sys.exit(1)

    logger.info(f"\nAll outputs saved to: {args.output_dir}")


if __name__ == "__main__":
    main()
