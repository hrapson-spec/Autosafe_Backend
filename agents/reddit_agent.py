"""
Reddit AI Monitoring Agent for AutoSafe
=========================================

Monitors UK car/MOT subreddits for high-intent posts, generates helpful
responses using Claude API, and optionally posts them.

Usage:
    python -m agents.reddit_agent                    # Dry run (default)
    python -m agents.reddit_agent --post             # Actually post replies
    python -m agents.reddit_agent --max-replies 3    # Limit replies per run

Requires environment variables:
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD
    ANTHROPIC_API_KEY

Designed to run daily via cron:
    0 9 * * * cd /app && python -m agents.reddit_agent --post --max-replies 3
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "agent": "reddit", "message": "%(message)s"}',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ─── Configuration ─────────────────────────────────────────────────
SUBREDDITS = ["CarTalkUK", "MechanicAdvice", "AskUK"]

# Keywords that signal MOT-related intent
MOT_KEYWORDS = [
    r"\bMOT\b", r"\bmot\b", r"mot fail", r"mot pass", r"mot test",
    r"pre-mot", r"pre mot", r"mot due", r"mot expir",
    r"mot advisory", r"mot check", r"annual test",
    r"roadworthy", r"mot preparation", r"mot cost",
]
MOT_PATTERN = re.compile("|".join(MOT_KEYWORDS), re.IGNORECASE)

# High-intent phrases that make a post worth responding to
HIGH_INTENT_PHRASES = [
    r"will (it|my car) (pass|fail)",
    r"chances of (passing|failing)",
    r"should I (fix|repair|worry|be concerned)",
    r"how (likely|probable)",
    r"is (this|it) (worth|going to)",
    r"any advice.*(mot|test)",
    r"first (mot|test)",
    r"about to (take|go for|book).*(mot|test)",
    r"failed.*(mot|test)",
    r"advisory.*(mot|test)",
    r"what (are|were) the (common|main|usual).*(fail|problem|issue)",
    r"(reliable|reliability).*(mot|test)",
]
HIGH_INTENT_PATTERN = re.compile("|".join(HIGH_INTENT_PHRASES), re.IGNORECASE)

# Rate limits
MAX_REPLIES_PER_RUN = 5
MIN_POST_AGE_HOURS = 1
MAX_POST_AGE_HOURS = 48
REPLY_DELAY_SECONDS = 120  # 2 minutes between replies

# Log file for tracking what we've responded to
LOG_DIR = Path(__file__).parent.parent / "logs"
RESPONSE_LOG = LOG_DIR / "reddit_agent_responses.jsonl"
SEEN_LOG = LOG_DIR / "reddit_agent_seen.json"

SYSTEM_PROMPT = """You are a helpful UK car enthusiast who knows a lot about MOTs and vehicle maintenance.
You're writing a Reddit comment responding to someone's question about MOT tests, car reliability, or vehicle maintenance.

Rules:
- Answer their actual question first with genuine, helpful advice
- Be conversational and friendly, like a knowledgeable mate at the pub
- Keep it concise (2-4 short paragraphs max)
- If relevant, mention that tools like AutoSafe (autosafe.one) can predict MOT failure risk based on DVSA data — but ONLY if it genuinely fits the conversation
- Never be pushy or salesy. If AutoSafe isn't relevant, don't mention it at all
- Use British English spelling (colour, tyres, etc.)
- Don't use emojis
- Don't start with "Great question!" or similar filler
- Sound like a real person, not a corporate account"""


def _load_seen_ids() -> set:
    """Load previously seen post IDs to avoid duplicate responses."""
    if SEEN_LOG.exists():
        try:
            data = json.loads(SEEN_LOG.read_text())
            return set(data.get("seen_ids", []))
        except (json.JSONDecodeError, KeyError):
            pass
    return set()


def _save_seen_ids(seen_ids: set):
    """Save seen post IDs."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Keep only last 1000 IDs to prevent unbounded growth
    ids = list(seen_ids)[-1000:]
    SEEN_LOG.write_text(json.dumps({"seen_ids": ids, "updated": datetime.now(timezone.utc).isoformat()}))


def _log_response(post_id: str, subreddit: str, title: str, response: str, posted: bool):
    """Append a response to the JSONL log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "post_id": post_id,
        "subreddit": subreddit,
        "title": title,
        "response_preview": response[:200],
        "posted": posted,
    }
    with open(RESPONSE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _is_high_intent(title: str, body: str) -> bool:
    """Check if a post has high MOT-related intent."""
    text = f"{title} {body}"
    # Must mention MOT or related terms
    if not MOT_PATTERN.search(text):
        return False
    # Bonus: check for question-like high intent
    if HIGH_INTENT_PATTERN.search(text):
        return True
    # Also accept direct questions with MOT keywords
    if "?" in text and MOT_PATTERN.search(text):
        return True
    return False


def find_relevant_posts(reddit, max_posts: int = 50) -> list:
    """Find relevant MOT-related posts across monitored subreddits."""
    seen_ids = _load_seen_ids()
    relevant = []
    now = datetime.now(timezone.utc)

    for sub_name in SUBREDDITS:
        try:
            subreddit = reddit.subreddit(sub_name)
            for post in subreddit.new(limit=max_posts):
                # Skip if already seen
                if post.id in seen_ids:
                    continue

                # Check post age
                post_time = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                age_hours = (now - post_time).total_seconds() / 3600

                if age_hours < MIN_POST_AGE_HOURS or age_hours > MAX_POST_AGE_HOURS:
                    continue

                # Skip if locked, removed, or already has many comments
                if post.locked or post.removed_by_category:
                    continue

                # Check relevance
                body = post.selftext or ""
                if _is_high_intent(post.title, body):
                    relevant.append({
                        "id": post.id,
                        "subreddit": sub_name,
                        "title": post.title,
                        "body": body[:2000],  # Truncate long posts
                        "url": f"https://reddit.com{post.permalink}",
                        "age_hours": round(age_hours, 1),
                        "num_comments": post.num_comments,
                        "score": post.score,
                        "post_obj": post,
                    })

                seen_ids.add(post.id)

        except Exception as e:
            logger.error(f"Error scanning r/{sub_name}: {e}")

    _save_seen_ids(seen_ids)

    # Sort by score (engagement) descending, then by fewer comments (more room to add value)
    relevant.sort(key=lambda p: (-p["score"], p["num_comments"]))
    return relevant


def generate_response(post: dict) -> str:
    """Generate a helpful response using Claude API."""
    import anthropic

    client = anthropic.Anthropic()

    user_prompt = f"""Reddit post in r/{post['subreddit']}:

Title: {post['title']}

{post['body']}

Write a helpful Reddit comment responding to this post."""

    message = client.messages.create(
        model="claude-sonnet-4-5-20250514",
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return message.content[0].text


def run_agent(post_replies: bool = False, max_replies: int = MAX_REPLIES_PER_RUN):
    """Main agent loop."""
    import praw

    # Validate credentials
    reddit_creds = {
        "client_id": os.environ.get("REDDIT_CLIENT_ID"),
        "client_secret": os.environ.get("REDDIT_CLIENT_SECRET"),
        "username": os.environ.get("REDDIT_USERNAME"),
        "password": os.environ.get("REDDIT_PASSWORD"),
    }

    if not all(reddit_creds.values()):
        missing = [k for k, v in reddit_creds.items() if not v]
        logger.error(f"Missing Reddit credentials: {missing}")
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("Missing ANTHROPIC_API_KEY")
        sys.exit(1)

    # Initialize Reddit client
    reddit = praw.Reddit(
        client_id=reddit_creds["client_id"],
        client_secret=reddit_creds["client_secret"],
        username=reddit_creds["username"],
        password=reddit_creds["password"],
        user_agent="AutoSafe MOT Helper v1.0 (by /u/" + reddit_creds["username"] + ")",
    )

    mode = "POST" if post_replies else "DRY RUN"
    logger.info(f"Starting Reddit agent ({mode}, max {max_replies} replies)")

    # Find relevant posts
    posts = find_relevant_posts(reddit)
    logger.info(f"Found {len(posts)} relevant posts")

    if not posts:
        logger.info("No relevant posts found. Done.")
        return {"found": 0, "replied": 0, "skipped": 0}

    replied = 0
    skipped = 0

    for post in posts[:max_replies]:
        logger.info(f"Processing: r/{post['subreddit']} - \"{post['title'][:60]}\" (score={post['score']}, comments={post['num_comments']})")

        try:
            response = generate_response(post)
            logger.info(f"Generated response ({len(response)} chars)")

            if post_replies:
                post["post_obj"].reply(response)
                logger.info(f"Posted reply to {post['url']}")
                _log_response(post["id"], post["subreddit"], post["title"], response, posted=True)
                replied += 1

                # Rate limit between posts
                if replied < max_replies:
                    logger.info(f"Waiting {REPLY_DELAY_SECONDS}s before next reply...")
                    time.sleep(REPLY_DELAY_SECONDS)
            else:
                logger.info(f"[DRY RUN] Would reply to: {post['url']}")
                logger.info(f"[DRY RUN] Response preview:\n{response[:300]}...")
                _log_response(post["id"], post["subreddit"], post["title"], response, posted=False)
                replied += 1

        except Exception as e:
            logger.error(f"Error processing post {post['id']}: {e}")
            skipped += 1

    result = {"found": len(posts), "replied": replied, "skipped": skipped}
    logger.info(f"Agent complete: {result}")
    return result


def main():
    parser = argparse.ArgumentParser(description="AutoSafe Reddit monitoring agent")
    parser.add_argument("--post", action="store_true", help="Actually post replies (default: dry run)")
    parser.add_argument("--max-replies", type=int, default=MAX_REPLIES_PER_RUN, help=f"Max replies per run (default: {MAX_REPLIES_PER_RUN})")
    args = parser.parse_args()

    run_agent(post_replies=args.post, max_replies=args.max_replies)


if __name__ == "__main__":
    main()
