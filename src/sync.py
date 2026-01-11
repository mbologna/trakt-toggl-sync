import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

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

# Constants
TRAKT_TOKEN_FILE = os.getenv("TRAKT_TOKEN_FILE", ".trakt_tokens.json")
TRAKT_TOKEN_EXPIRATION_BUFFER_MINUTES = 60
TRAKT_API_HEADERS = {
    "Content-Type": "application/json",
    "trakt-api-version": "2",
    "trakt-api-key": TRAKT_CLIENT_ID,
}


def load_json_file(file_path):
    """Load JSON data from file."""
    if os.path.exists(file_path):
        with open(file_path) as f:
            return json.load(f)
    return None


def save_json_file(file_path, data):
    """Save JSON data to file with restricted permissions."""
    # Ensure directory exists
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(file_path, 0o600)


def timestamp():
    """Generate current timestamp for logging."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def check_required_env_variables():
    """Validate all required environment variables are set."""
    required_env_vars = [
        "TRAKT_CLIENT_ID",
        "TRAKT_CLIENT_SECRET",
        "TOGGL_API_TOKEN",
        "TOGGL_WORKSPACE_ID",
        "TOGGL_PROJECT_ID",
    ]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars:
        print(
            f"[{timestamp()}] Error: Missing required environment variables: {', '.join(missing_vars)}",
            file=sys.stderr,
        )
        sys.exit(1)


class TraktAPI:
    """Trakt API client for managing viewing history."""

    BASE_URL = "https://api.trakt.tv"

    @staticmethod
    def is_token_near_expiration(expiration_time):
        """Check if token is near expiration."""
        now = datetime.now()
        expiration = datetime.fromisoformat(expiration_time)
        return now >= expiration - timedelta(minutes=TRAKT_TOKEN_EXPIRATION_BUFFER_MINUTES)

    @staticmethod
    def authenticate():
        """Authenticate with Trakt via device flow."""
        response = requests.post(
            f"{TraktAPI.BASE_URL}/oauth/device/code",
            json={"client_id": TRAKT_CLIENT_ID},
            headers=TRAKT_API_HEADERS,
        )
        response.raise_for_status()
        device_data = response.json()

        print(f"[{timestamp()}] Visit {device_data['verification_url']} and enter the code: {device_data['user_code']}")

        while True:
            time.sleep(device_data["interval"])
            response = requests.post(
                f"{TraktAPI.BASE_URL}/oauth/device/token",
                json={"client_id": TRAKT_CLIENT_ID, "code": device_data["device_code"]},
            )
            if response.status_code == 200:
                tokens = response.json()
                tokens["expires_at"] = (datetime.now() + timedelta(seconds=tokens["expires_in"])).isoformat()
                save_json_file(TRAKT_TOKEN_FILE, tokens)
                print(f"[{timestamp()}] Authentication successful!")
                return tokens
            elif response.status_code in {400, 404, 410, 418, 429}:
                print(f"[{timestamp()}] Waiting for user authentication...")
            else:
                print(f"[{timestamp()}] Authentication failed: {response.status_code}")
                break
        raise RuntimeError(f"[{timestamp()}] Authentication failed.")

    @staticmethod
    def refresh_token(refresh_token):
        """Refresh Trakt access token."""
        try:
            response = requests.post(
                f"{TraktAPI.BASE_URL}/oauth/token",
                json={
                    "client_id": TRAKT_CLIENT_ID,
                    "client_secret": TRAKT_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            response.raise_for_status()
            tokens = response.json()
            tokens["expires_at"] = (datetime.now() + timedelta(seconds=tokens["expires_in"])).isoformat()
            save_json_file(TRAKT_TOKEN_FILE, tokens)
            print(f"[{timestamp()}] Token refreshed successfully!")
            return tokens
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                print(f"[{timestamp()}] Refresh token expired. Re-authenticating...")
                if os.path.exists(TRAKT_TOKEN_FILE):
                    os.remove(TRAKT_TOKEN_FILE)
                return TraktAPI.authenticate()
            else:
                raise

    @staticmethod
    def get_headers(access_token):
        """Get API headers with authorization."""
        return {**TRAKT_API_HEADERS, "Authorization": f"Bearer {access_token}"}

    @staticmethod
    def fetch_full_history(access_token):
        """Fetch complete viewing history from Trakt."""
        headers = TraktAPI.get_headers(access_token)
        history = []
        page = 1

        print(f"[{timestamp()}] Fetching complete Trakt history...")
        while True:
            response = requests.get(
                f"{TraktAPI.BASE_URL}/sync/history", headers=headers, params={"page": page, "limit": 1000}
            )
            response.raise_for_status()
            data = response.json()
            if not data:
                break
            history.extend(data)
            page += 1

        print(f"[{timestamp()}] Fetched {len(history)} total Trakt history entries")
        return history

    @staticmethod
    def format_entry_description(entry):
        """Format entry for display."""
        if entry["type"] == "movie":
            movie = entry.get("movie", {})
            return f"🎞️ {movie.get('title', 'Unknown')} ({movie.get('year', 'N/A')})"
        else:
            show = entry.get("show", {})
            episode = entry.get("episode", {})
            return f"📺 {show.get('title', 'Unknown')} - S{episode.get('season', 0):02}E{episode.get('number', 0):02}"

    @staticmethod
    def remove_duplicates(access_token):
        """Remove duplicate entries from Trakt history, keeping most recent."""
        print(f"[{timestamp()}] Starting Trakt deduplication...")
        history = TraktAPI.fetch_full_history(access_token)

        unique_items = {}
        for entry in history:
            # Create unique key based on type and ID
            if entry["type"] == "movie":
                item_key = ("movie", entry.get("movie", {}).get("ids", {}).get("trakt"))
            else:
                item_key = ("episode", entry.get("episode", {}).get("ids", {}).get("trakt"))

            if not item_key[1]:
                continue

            # Keep entry with most recent watched_at date
            if item_key not in unique_items or unique_items[item_key]["watched_at"] < entry["watched_at"]:
                unique_items[item_key] = entry

        duplicates = [entry for entry in history if entry not in unique_items.values()]

        if duplicates:
            print(f"[{timestamp()}] Found {len(duplicates)} duplicate Trakt entries to remove:")
            for dup in duplicates:
                desc = TraktAPI.format_entry_description(dup)
                watched = dup["watched_at"][:10]
                print(f"  - {desc} (watched: {watched})")

            headers = TraktAPI.get_headers(access_token)
            payload = {"ids": [entry["id"] for entry in duplicates]}
            response = requests.post(f"{TraktAPI.BASE_URL}/sync/history/remove", headers=headers, json=payload)
            if response.status_code == 200:
                print(f"[{timestamp()}] ✓ Successfully removed {len(duplicates)} duplicate Trakt entries")
            else:
                print(f"[{timestamp()}] ✗ Failed to delete Trakt duplicates: {response.status_code}", file=sys.stderr)
        else:
            print(f"[{timestamp()}] No Trakt duplicates found")

    @staticmethod
    def fetch_history(access_token, start_date):
        """Fetch viewing history from Trakt starting from a specific date."""
        headers = TraktAPI.get_headers(access_token)
        history = []
        page = 1

        while True:
            response = requests.get(
                f"{TraktAPI.BASE_URL}/sync/history?extended=full",
                headers=headers,
                params={"start_at": start_date, "page": page, "limit": 100},
            )
            response.raise_for_status()
            data = response.json()
            if not data:
                break
            history.extend(data)
            page += 1

        return history


class TogglAPI:
    """Toggl API client for time tracking."""

    BASE_URL = "https://api.track.toggl.com/api/v9"
    _cached_entries = None
    _cache_timestamp = None
    _cache_duration = 300  # Cache for 5 minutes
    _rate_limited = False  # Track if we're rate limited

    @staticmethod
    def parse_time(time_str):
        """Parse Toggl time strings to datetime."""
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"
        return datetime.fromisoformat(time_str)

    @staticmethod
    def normalize_timestamp(timestamp):
        """Normalize timestamps for comparison."""
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(microsecond=0)

    @staticmethod
    def get_cached_entries(force_refresh=False):
        """Get cached Toggl entries or fetch if cache is stale."""
        now = time.time()
        if (
            force_refresh
            or TogglAPI._cached_entries is None
            or TogglAPI._cache_timestamp is None
            or now - TogglAPI._cache_timestamp > TogglAPI._cache_duration
        ):
            try:
                response = requests.get(
                    f"{TogglAPI.BASE_URL}/me/time_entries",
                    auth=(TOGGL_API_TOKEN, "api_token"),
                )
                response.raise_for_status()
                TogglAPI._cached_entries = response.json()
                TogglAPI._cache_timestamp = now
                TogglAPI._rate_limited = False
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 402:
                    if not TogglAPI._rate_limited:
                        print(f"[{timestamp()}] ⚠ Toggl rate limit reached.")
                        print(
                            f"[{timestamp()}] Cannot check for duplicates - will skip creating entries to avoid duplicates."
                        )
                        TogglAPI._rate_limited = True
                    # Return None to signal rate limiting
                    return None
                else:
                    raise

        return TogglAPI._cached_entries

    @staticmethod
    def remove_duplicates():
        """Remove duplicate entries from Toggl, keeping most recent."""
        print(f"[{timestamp()}] Starting Toggl deduplication...")

        try:
            # Fetch all entries for the project from the last year
            today = datetime.now()
            one_year_ago = today - timedelta(days=365)
            all_entries = []
            current_before = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            while True:
                params = {"before": current_before}
                try:
                    response = requests.get(
                        f"{TogglAPI.BASE_URL}/me/time_entries", params=params, auth=(TOGGL_API_TOKEN, "api_token")
                    )
                    response.raise_for_status()
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 402:
                        print(f"[{timestamp()}] ⚠ Toggl rate limit reached (402). Skipping deduplication.")
                        print(f"[{timestamp()}] This is temporary - try again in a few minutes.")
                        return
                    raise

                batch = response.json()

                if not batch:
                    break

                project_entries = [e for e in batch if e.get("project_id") == TOGGL_PROJECT_ID]
                all_entries.extend(project_entries)

                oldest_entry = min(batch, key=lambda x: x.get("start", ""))
                oldest_time = TogglAPI.parse_time(oldest_entry["start"]).replace(tzinfo=None)

                if oldest_time < one_year_ago:
                    break

                current_before = (TogglAPI.parse_time(oldest_entry["start"]) - timedelta(milliseconds=1)).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )

            # Filter to last year only
            filtered_entries = [
                e for e in all_entries if TogglAPI.parse_time(e["start"]).replace(tzinfo=None) >= one_year_ago
            ]

            print(f"[{timestamp()}] Found {len(filtered_entries)} Toggl entries in project")

            # Find duplicates by description
            entries_by_description = {}
            for entry in filtered_entries:
                desc = entry.get("description", "")
                if desc:
                    if desc not in entries_by_description:
                        entries_by_description[desc] = []
                    entries_by_description[desc].append(entry)

            duplicates = {desc: entries for desc, entries in entries_by_description.items() if len(entries) > 1}

            if duplicates:
                total_deleted = 0
                entries_to_delete_count = sum(len(entries) - 1 for entries in duplicates.values())
                print(f"[{timestamp()}] Found {entries_to_delete_count} duplicate Toggl entries to remove:")

                for description, entries in duplicates.items():
                    print(f"  - {description} ({len(entries)} occurrences)")
                    entries.sort(key=lambda x: x.get("start", ""))
                    entries_to_delete = entries[:-1]

                    for entry in entries_to_delete:
                        start = TogglAPI.parse_time(entry["start"]).strftime("%Y-%m-%d %H:%M")
                        response = requests.delete(
                            f"{TogglAPI.BASE_URL}/time_entries/{entry['id']}", auth=(TOGGL_API_TOKEN, "api_token")
                        )
                        if response.status_code == 200:
                            print(f"    ✓ Deleted: {start}")
                            total_deleted += 1
                        else:
                            print(f"    ✗ Failed to delete: {start} - {response.status_code}", file=sys.stderr)

                print(f"[{timestamp()}] Successfully removed {total_deleted} duplicate Toggl entries")
                # Clear cache after deletion
                TogglAPI._cached_entries = None
            else:
                print(f"[{timestamp()}] No Toggl duplicates found")

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 402:
                print(f"[{timestamp()}] ⚠ Toggl rate limit reached. Skipping deduplication.")
                print(f"[{timestamp()}] This is temporary - try again in a few minutes.")
            else:
                raise

    @staticmethod
    def entry_exists(description, start_time, end_time):
        """Check if entry exists using cached data."""
        entries = TogglAPI.get_cached_entries()

        # If rate limited, we can't check, so return True to skip creation
        if entries is None:
            return True  # Assume exists to avoid creating duplicates

        start_time = TogglAPI.normalize_timestamp(start_time)
        end_time = TogglAPI.normalize_timestamp(end_time)

        for entry in entries:
            entry_start = TogglAPI.normalize_timestamp(entry["start"])
            entry_end = TogglAPI.normalize_timestamp(entry["stop"]) if entry.get("stop") else None

            if (
                entry["description"] == description
                and entry_start == start_time
                and (entry_end == end_time if entry_end else False)
                and entry.get("project_id") == TOGGL_PROJECT_ID
                and set(entry.get("tags", [])) == set(TOGGL_TAGS)
                and entry.get("wid") == TOGGL_WORKSPACE_ID
            ):
                return True
        return False

    @staticmethod
    def create_entry(description, start_time, end_time):
        """Create a new Toggl time entry."""
        if TogglAPI.entry_exists(description, start_time, end_time):
            if TogglAPI._rate_limited:
                print(f"[{timestamp()}] Skipped (rate limited): {description}")
            else:
                print(f"[{timestamp()}] Skipped (exists): {description}")
            return

        data = {
            "description": description,
            "start": start_time,
            "stop": end_time,
            "created_with": "trakt-toggl-sync",
            "project_id": TOGGL_PROJECT_ID,
            "tags": TOGGL_TAGS,
            "wid": TOGGL_WORKSPACE_ID,
        }

        try:
            response = requests.post(
                f"{TogglAPI.BASE_URL}/workspaces/{TOGGL_WORKSPACE_ID}/time_entries",
                json=data,
                auth=(TOGGL_API_TOKEN, "api_token"),
            )
            response.raise_for_status()
            start_dt = TogglAPI.parse_time(start_time).strftime("%Y-%m-%d %H:%M")
            print(f"[{timestamp()}] ✓ Created: {description} (at {start_dt})")
            # Invalidate cache after creating entry
            TogglAPI._cached_entries = None
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 402:
                print(f"[{timestamp()}] ⚠ Rate limit on creation. Stopping sync.")
                TogglAPI._rate_limited = True
                # Re-raise to stop the sync process
                raise
            else:
                print(f"[{timestamp()}] ✗ Failed to create: {description} - {e.response.text}", file=sys.stderr)


def process_history_item(item):
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

    TogglAPI.create_entry(
        description=title,
        start_time=start_time.isoformat() + "Z",
        end_time=watched_at,
    )


def main():
    """Main sync process."""
    print(f"[{timestamp()}] ===== Starting trakt-toggl-sync =====")

    check_required_env_variables()

    # Handle Trakt authentication
    tokens = load_json_file(TRAKT_TOKEN_FILE)
    if not tokens:
        tokens = TraktAPI.authenticate()
    elif TraktAPI.is_token_near_expiration(tokens["expires_at"]):
        tokens = TraktAPI.refresh_token(tokens["refresh_token"])

    # Step 1: Remove Trakt duplicates
    print(f"\n[{timestamp()}] === Step 1: Removing Trakt Duplicates ===")
    TraktAPI.remove_duplicates(tokens["access_token"])

    # Step 2: Remove Toggl duplicates
    print(f"\n[{timestamp()}] === Step 2: Removing Toggl Duplicates ===")
    TogglAPI.remove_duplicates()

    # Step 3: Sync from Trakt to Toggl
    print(f"\n[{timestamp()}] === Step 3: Syncing Trakt to Toggl ===")
    print(f"[{timestamp()}] Fetching Trakt history for the last {TRAKT_HISTORY_DAYS} days...")
    start_date = (datetime.now() - timedelta(days=TRAKT_HISTORY_DAYS)).isoformat() + "Z"
    history = TraktAPI.fetch_history(tokens["access_token"], start_date)

    print(f"[{timestamp()}] Processing {len(history)} entries...")
    try:
        for item in history:
            process_history_item(item)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 402:
            print(f"[{timestamp()}] ⚠ Sync stopped due to rate limits.")
            print(f"[{timestamp()}] Run again later to sync remaining entries.")

    print(f"\n[{timestamp()}] ===== Sync Complete =====")


if __name__ == "__main__":
    main()
