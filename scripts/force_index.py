"""
Force-index AutoSafe sitemap URLs via IndexNow protocol.

Fetches the live sitemap, prioritizes viral/high-value pages, and POSTs
directly to the IndexNow API so Bing/Yandex/Seznam/Naver discover them
immediately instead of waiting for a crawl cycle.

Usage:
    python scripts/force_index.py

    # Or with an explicit key:
    INDEXNOW_KEY=autosafe-indexnow-key python scripts/force_index.py
"""

import os
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SITE_HOST = "www.autosafe.one"
SITEMAP_URL = f"https://{SITE_HOST}/sitemap.xml"
KEY_VERIFICATION_URL = f"https://{SITE_HOST}/indexnow-key.txt"
INDEXNOW_API_URL = "https://api.indexnow.org/indexnow"
BATCH_SIZE = 10_000
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
PRIORITY_PREFIXES = ("/insights/", "/will-my-car-pass-mot/")


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------
def get_indexnow_key() -> str:
    """Read IndexNow key from env, falling back to the production default."""
    key = os.environ.get("INDEXNOW_KEY", "autosafe-indexnow-key")
    print(f"[key] Using IndexNow key: {key[:8]}...")
    return key


def preflight_check(client: httpx.Client, key: str) -> bool:
    """Verify the key-verification endpoint is live and returns the key."""
    print(f"[preflight] GET {KEY_VERIFICATION_URL}")
    try:
        resp = client.get(KEY_VERIFICATION_URL, timeout=10)
    except httpx.ConnectError as exc:
        print(f"[preflight] FAIL — could not connect: {exc}")
        return False

    if resp.status_code != 200:
        print(f"[preflight] FAIL — status {resp.status_code} (expected 200)")
        _print_route_hint()
        return False

    body = resp.text.strip()
    if body != key:
        print(f"[preflight] FAIL — response body '{body}' does not match key '{key}'")
        _print_route_hint()
        return False

    print("[preflight] OK — key verification endpoint matches")
    return True


def _print_route_hint() -> None:
    print(
        "\n  Hint: make sure main.py has this route:\n"
        "\n"
        '    @app.get("/indexnow-key.txt")\n'
        "    async def indexnow_key_file():\n"
        '        return Response(content=INDEXNOW_KEY, media_type="text/plain")\n'
    )


def fetch_sitemap_urls(client: httpx.Client) -> list[str]:
    """Fetch and parse the live sitemap XML, returning all <loc> URLs."""
    print(f"[sitemap] GET {SITEMAP_URL}")
    resp = client.get(SITEMAP_URL, timeout=15)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    urls = [loc.text for loc in root.findall(".//sm:loc", SITEMAP_NS) if loc.text]
    print(f"[sitemap] Found {len(urls)} URLs")
    return urls


def prioritize_urls(urls: list[str]) -> list[str]:
    """Sort URLs so high-value pages (insights, will-my-car-pass) come first."""
    priority = []
    rest = []
    for url in urls:
        path = urlparse(url).path
        if any(path.startswith(p) for p in PRIORITY_PREFIXES):
            priority.append(url)
        else:
            rest.append(url)
    print(f"[priority] {len(priority)} priority URLs, {len(rest)} other URLs")
    return priority + rest


def submit_to_indexnow(client: httpx.Client, key: str, urls: list[str]) -> None:
    """POST URLs to the IndexNow API in batches."""
    total = len(urls)
    submitted = 0

    for i in range(0, total, BATCH_SIZE):
        batch = urls[i : i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        print(f"[indexnow] Submitting batch {batch_num} ({len(batch)} URLs)...")

        payload = {
            "host": SITE_HOST,
            "key": key,
            "keyLocation": KEY_VERIFICATION_URL,
            "urlList": batch,
        }

        resp = client.post(INDEXNOW_API_URL, json=payload, timeout=30)
        status = resp.status_code

        if status in (200, 202):
            print(f"[indexnow] Batch {batch_num}: {status} OK")
            submitted += len(batch)
        elif status == 403:
            print(f"[indexnow] Batch {batch_num}: 403 Forbidden — key mismatch or not verified")
            sys.exit(1)
        elif status == 429:
            print(f"[indexnow] Batch {batch_num}: 429 Too Many Requests — try again later")
            sys.exit(1)
        else:
            print(f"[indexnow] Batch {batch_num}: unexpected status {status}")
            print(f"           body: {resp.text[:500]}")

    print(f"\n[done] {submitted}/{total} URLs submitted to IndexNow")


def print_gsc_instructions() -> None:
    """Print manual Google Search Console steps for top priority pages."""
    pages = [
        "https://www.autosafe.one/insights/march-mot-rush-2026/",
        "https://www.autosafe.one/will-my-car-pass-mot/",
        "https://www.autosafe.one/mot-check/ford/fiesta/",
        "https://www.autosafe.one/mot-check/vauxhall/corsa/",
        "https://www.autosafe.one/mot-check/volkswagen/golf/",
    ]
    print("\n" + "=" * 60)
    print("GOOGLE SEARCH CONSOLE — manual steps (~10 URLs/day limit)")
    print("=" * 60)
    print("\n1. Open https://search.google.com/search-console")
    print("2. Select the 'www.autosafe.one' property")
    print("3. Paste each URL into the URL Inspection bar at the top")
    print('4. Click "Request Indexing"\n')
    print("Priority pages to submit first:\n")
    for i, page in enumerate(pages, 1):
        print(f"  {i}. {page}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 60)
    print("AutoSafe — IndexNow Force-Indexing")
    print("=" * 60 + "\n")

    key = get_indexnow_key()

    with httpx.Client() as client:
        # Pre-flight
        if not preflight_check(client, key):
            answer = input("\nPre-flight failed. Continue anyway? [y/N] ").strip().lower()
            if answer != "y":
                print("Aborted.")
                sys.exit(1)

        # Fetch & prioritize
        urls = fetch_sitemap_urls(client)
        if not urls:
            print("[error] No URLs found in sitemap — nothing to submit.")
            sys.exit(1)

        urls = prioritize_urls(urls)

        # Submit
        submit_to_indexnow(client, key, urls)

    # Google Search Console manual steps
    print_gsc_instructions()


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError as exc:
        print(f"\n[error] Connection failed: {exc}")
        sys.exit(1)
    except httpx.TimeoutException as exc:
        print(f"\n[error] Request timed out: {exc}")
        sys.exit(1)
    except ET.ParseError as exc:
        print(f"\n[error] Failed to parse sitemap XML: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
        sys.exit(130)
