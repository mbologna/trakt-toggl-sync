"""Main sync script for trakt-toggl-sync."""

import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from toggl import TogglAPI
from trakt import TraktAPI
from utils import check_required_env_variables, load_json_file, timestamp

# Load environment variables
load_dotenv()

# Configuration
TRAKT_CLIENT_ID = os.getenv("TRAKT_CLIENT_ID")
TRAKT_CLIENT_SECRET = os.getenv("TRAKT_CLIENT_SECRET")
TRAKT_HISTORY_DAYS = int(os.getenv("TRAKT_HISTORY_DAYS", 7))
TOGGL_API_TOKEN = os.getenv("TOGGL_API_TOKEN")
TOGGL_WORKSPACE_ID = int(os.getenv("TOGGL_WORKSPACE_ID")) if os.getenv("TOGGL_WORKSPACE_ID") else None
TOGGL_PROJECT_ID = int(os.getenv("TOGGL_PROJECT_ID")) if os.getenv("TOGGL_PROJECT_ID") else None
TOGGL_TAGS = [tag.strip() for tag in os.getenv("TOGGL_TAGS", "").split(",") if tag.strip()]

# Token file location
TRAKT_TOKEN_FILE = os.getenv("TRAKT_TOKEN_FILE", ".trakt_tokens.json")


def process_history_item(item, toggl_api):
    """Process a single history item and create Toggl entry."""
    watched_at = item["watched_at"]
    item_type = item["type"]

    if item_type == "episode":
        title = (
            f"📺 {item['show']['title']} - "
            f"S{item['episode']['season']:02}E{item['episode']['number']:02} - "
            f"{item['episode']['title']}"
        )
        runtime = item["episode"]["runtime"]
    else:
        title = f"🎞️ {item['movie']['title']} ({item['movie'].get('year', 'N/A')})"
        runtime = item["movie"].get("runtime", 0)

    end_time = datetime.fromisoformat(watched_at[:-1])
    start_time = end_time - timedelta(minutes=runtime)

    toggl_api.create_entry(
        description=title,
        start_time=start_time.isoformat() + "Z",
        end_time=watched_at,
    )


def main():
    """Main sync process."""
    print(f"[{timestamp()}] ===== Starting trakt-toggl-sync =====")

    check_required_env_variables()

    # Initialize API clients
    trakt = TraktAPI(TRAKT_CLIENT_ID, TRAKT_CLIENT_SECRET, TRAKT_TOKEN_FILE)
    toggl = TogglAPI(TOGGL_API_TOKEN, TOGGL_WORKSPACE_ID, TOGGL_PROJECT_ID, TOGGL_TAGS)

    # Handle Trakt authentication
    tokens = load_json_file(TRAKT_TOKEN_FILE)
    if not tokens:
        tokens = trakt.authenticate()
    elif trakt.is_token_near_expiration(tokens["expires_at"]):
        tokens = trakt.refresh_token(tokens["refresh_token"])

    # Step 1: Remove Trakt duplicates
    print(f"\n[{timestamp()}] === Step 1: Removing Trakt Duplicates ===")
    trakt.remove_duplicates(tokens["access_token"])

    # Step 2: Remove Toggl duplicates
    print(f"\n[{timestamp()}] === Step 2: Removing Toggl Duplicates ===")
    toggl.remove_duplicates()

    # Step 3: Sync from Trakt to Toggl
    print(f"\n[{timestamp()}] === Step 3: Syncing Trakt to Toggl ===")
    print(f"[{timestamp()}] Fetching Trakt history for the last {TRAKT_HISTORY_DAYS} days...")
    start_date = (datetime.now() - timedelta(days=TRAKT_HISTORY_DAYS)).isoformat() + "Z"
    history = trakt.fetch_history(tokens["access_token"], start_date)

    print(f"[{timestamp()}] Processing {len(history)} entries...")
    try:
        for item in history:
            process_history_item(item, toggl)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 402:
            print(f"[{timestamp()}] ⚠ Sync stopped due to rate limits.")
            print(f"[{timestamp()}] Run again later to sync remaining entries.")

    print(f"\n[{timestamp()}] ===== Sync Complete =====")


if __name__ == "__main__":
    main()
