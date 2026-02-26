"""
Data Story Auto-Publisher for AutoSafe
========================================

Weekly cron job that:
1. Generates fresh data stories from all 4 story types
2. Saves outputs (HTML, markdown, charts, JSON) to data_stories/output/
3. Generates social-ready text snippets for distribution
4. Emails press pitches to media contact list

Usage:
    python -m agents.data_story_publisher                  # Dry run
    python -m agents.data_story_publisher --publish        # Generate + email pitches
    python -m agents.data_story_publisher --generate-only  # Generate stories only

Designed to run weekly via cron:
    0 8 * * 1 cd /app && python -m agents.data_story_publisher --publish
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_stories.generate import generate_story
from data_stories.query_engine import STORY_QUERIES
from data_stories.story_templates import render_pitch_email
from email_service import send_email, is_configured as email_configured

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "agent": "data_story_publisher", "message": "%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "data_stories" / "output"
SOCIAL_DIR = OUTPUT_DIR / "social"

# Media contact list - add emails here
# Format: [{"name": "...", "email": "...", "publication": "..."}]
MEDIA_CONTACTS_FILE = Path(__file__).parent.parent / "config" / "media_contacts.json"

# Press pitch "from" address
PRESS_FROM = os.environ.get("PRESS_EMAIL_FROM", "press@autosafe.one")


def load_media_contacts() -> list:
    """Load media contacts from JSON config file."""
    if not MEDIA_CONTACTS_FILE.exists():
        logger.info(f"No media contacts file at {MEDIA_CONTACTS_FILE}")
        return []
    try:
        contacts = json.loads(MEDIA_CONTACTS_FILE.read_text())
        logger.info(f"Loaded {len(contacts)} media contacts")
        return contacts
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Failed to load media contacts: {e}")
        return []


def generate_social_snippet(story: dict) -> dict:
    """Generate social media-ready text from a story."""
    title = story["title"]
    key_stat = story["key_stat"]
    slug = story["slug"]
    url = f"https://www.autosafe.one/insights/{slug}/"

    # Twitter/X format (under 280 chars)
    twitter = f"{key_stat}\n\nFull data from 142M+ DVSA records: {url}"
    if len(twitter) > 280:
        twitter = f"{key_stat[:200]}...\n\n{url}"

    # LinkedIn format (longer, more professional)
    linkedin = f"""{title}

{key_stat}

This is from our analysis of 142 million official DVSA MOT test records — the largest public MOT dataset in the UK.

Full methodology and data tables: {url}

#MOT #CarMaintenance #DataAnalysis #AutoSafe"""

    # Reddit format (for manual posting to relevant subreddits)
    reddit = f"""{title}

{key_stat}

We analysed 142M+ official DVSA MOT records. Full breakdown with charts and methodology: {url}"""

    return {
        "story_slug": slug,
        "twitter": twitter,
        "linkedin": linkedin,
        "reddit": reddit,
    }


def generate_all_stories() -> list:
    """Generate all data stories and return story data."""
    stories = []
    for story_name in STORY_QUERIES:
        logger.info(f"Generating story: {story_name}")
        try:
            story = generate_story(story_name, OUTPUT_DIR)
            stories.append(story)
            logger.info(f"  Generated: {story['title']}")
            logger.info(f"  Key stat: {story['key_stat']}")
        except Exception as e:
            logger.error(f"  Failed to generate '{story_name}': {e}")

    return stories


def generate_social_snippets(stories: list) -> list:
    """Generate social media snippets for all stories."""
    SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
    snippets = []

    for story in stories:
        snippet = generate_social_snippet(story)
        snippets.append(snippet)

        # Save individual snippet file
        snippet_path = SOCIAL_DIR / f"{story['slug']}-social.json"
        snippet_path.write_text(json.dumps(snippet, indent=2, ensure_ascii=False))
        logger.info(f"  Social snippet saved: {snippet_path}")

    # Save combined snippets file
    combined_path = SOCIAL_DIR / f"all-snippets-{date.today().isoformat()}.json"
    combined_path.write_text(json.dumps(snippets, indent=2, ensure_ascii=False))
    logger.info(f"All social snippets saved to: {combined_path}")

    return snippets


async def send_press_pitches(stories: list, dry_run: bool = True) -> dict:
    """Email press pitches for each story to media contacts."""
    contacts = load_media_contacts()
    if not contacts:
        logger.info("No media contacts configured - skipping press pitches")
        return {"sent": 0, "failed": 0, "skipped": 0}

    if not email_configured() and not dry_run:
        logger.error("Email service not configured - cannot send pitches")
        return {"sent": 0, "failed": 0, "skipped": len(contacts) * len(stories)}

    sent = 0
    failed = 0

    for story in stories:
        pitch_text = render_pitch_email(story)
        subject = f"Data story: {story['title']}"

        # Convert plain text pitch to simple HTML
        pitch_html = pitch_text.replace("\n", "<br>\n")
        pitch_html = f"<div style='font-family: sans-serif; line-height: 1.6; max-width: 600px;'>{pitch_html}</div>"

        for contact in contacts:
            personalised_pitch = pitch_text.replace("[Name]", contact.get("name", ""))
            personalised_pitch = personalised_pitch.replace("[publication]", contact.get("publication", "your publication"))
            personalised_html = personalised_pitch.replace("\n", "<br>\n")
            personalised_html = f"<div style='font-family: sans-serif; line-height: 1.6; max-width: 600px;'>{personalised_html}</div>"

            if dry_run:
                logger.info(f"[DRY RUN] Would email pitch to {contact.get('name', '?')} <{contact['email']}> re: {story['title']}")
                sent += 1
                continue

            try:
                success = await send_email(
                    to_email=contact["email"],
                    subject=subject,
                    html_body=personalised_html,
                    text_body=personalised_pitch,
                    tags={"type": "press_pitch", "story": story["slug"]},
                )
                if success:
                    sent += 1
                    logger.info(f"Pitch sent to {contact['email']}: {story['title']}")
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Failed to send pitch to {contact['email']}: {e}")
                failed += 1

    result = {"sent": sent, "failed": failed}
    logger.info(f"Press pitches complete: {result}")
    return result


async def run_publisher(publish: bool = False, generate_only: bool = False):
    """Main publisher pipeline."""
    logger.info(f"Starting data story publisher (publish={'yes' if publish else 'no'}, generate_only={generate_only})")

    # Step 1: Generate all stories
    stories = generate_all_stories()
    logger.info(f"Generated {len(stories)} stories")

    if not stories:
        logger.warning("No stories generated - exiting")
        return

    # Step 2: Generate social snippets
    snippets = generate_social_snippets(stories)
    logger.info(f"Generated {len(snippets)} social snippets")

    # Step 3: Print social snippets for easy copy-paste
    for snippet in snippets:
        logger.info(f"\n--- {snippet['story_slug']} ---")
        logger.info(f"Twitter:\n{snippet['twitter']}\n")

    if generate_only:
        logger.info("Generate-only mode - skipping press pitches")
        return

    # Step 4: Email press pitches
    dry_run = not publish
    pitch_result = await send_press_pitches(stories, dry_run=dry_run)

    # Log summary
    summary = {
        "date": datetime.now(timezone.utc).isoformat(),
        "stories_generated": len(stories),
        "social_snippets": len(snippets),
        "pitches": pitch_result,
        "mode": "publish" if publish else "dry_run",
    }
    logger.info(f"Publisher complete: {json.dumps(summary)}")

    # Save run log
    run_log_path = OUTPUT_DIR / "publisher_runs.jsonl"
    with open(run_log_path, "a") as f:
        f.write(json.dumps(summary) + "\n")


def main():
    parser = argparse.ArgumentParser(description="AutoSafe data story auto-publisher")
    parser.add_argument("--publish", action="store_true", help="Generate stories and send press pitches (default: dry run)")
    parser.add_argument("--generate-only", action="store_true", help="Generate stories and social snippets only, skip emails")
    args = parser.parse_args()

    asyncio.run(run_publisher(publish=args.publish, generate_only=args.generate_only))


if __name__ == "__main__":
    main()
