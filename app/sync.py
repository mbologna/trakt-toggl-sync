import os
import time
import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
import sys

# Load environment variables
load_dotenv()

# Configuration with safer handling of environment variables
TRAKT_CLIENT_ID = os.getenv("TRAKT_CLIENT_ID")
TRAKT_CLIENT_SECRET = os.getenv("TRAKT_CLIENT_SECRET")
TRAKT_HISTORY_DAYS = int(os.getenv("TRAKT_HISTORY_DAYS", 7))
TOGGL_API_TOKEN = os.getenv("TOGGL_API_TOKEN")
TOGGL_WORKSPACE_ID = os.getenv("TOGGL_WORKSPACE_ID")
TOGGL_PROJECT_ID = os.getenv("TOGGL_PROJECT_ID")
TOGGL_TAGS = os.getenv("TOGGL_TAGS", "").split(",")

# Fallback to default value or None if not set
TOGGL_WORKSPACE_ID = int(TOGGL_WORKSPACE_ID) if TOGGL_WORKSPACE_ID else None
TOGGL_PROJECT_ID = int(TOGGL_PROJECT_ID) if TOGGL_PROJECT_ID else None

# Constants
TRAKT_TOKEN_FILE = ".trakt_tokens.json"
TRAKT_TOKEN_EXPIRATION_BUFFER_MINUTES = 60
TRAKT_API_HEADERS = {
    "Content-Type": "application/json",
    "trakt-api-version": "2",
    "trakt-api-key": TRAKT_CLIENT_ID,
}


# Utility Functions
def load_json_file(file_path):
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    return None


def save_json_file(file_path, data):
    with open(file_path, "w") as f:
        json.dump(data, f)
    os.chmod(file_path, 0o600)  # Restrict file permissions


def timestamp():
    """Generate current timestamp for logging."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Function to check if all required environment variables are set
def check_required_env_variables():
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
        sys.exit(1)  # Exit the script if variables are missing


# Trakt API
class TraktAPI:
    BASE_URL = "https://api.trakt.tv"

    @staticmethod
    def is_token_near_expiration(expiration_time):
        now = datetime.now()
        expiration = datetime.fromisoformat(expiration_time)
        return now >= expiration - timedelta(
            minutes=TRAKT_TOKEN_EXPIRATION_BUFFER_MINUTES
        )

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

        print(
            f"[{timestamp()}] Visit {device_data['verification_url']} and enter the code: {device_data['user_code']}"
        )

        while True:
            time.sleep(device_data["interval"])
            response = requests.post(
                f"{TraktAPI.BASE_URL}/oauth/device/token",
                json={"client_id": TRAKT_CLIENT_ID, "code": device_data["device_code"]},
            )
            if response.status_code == 200:
                tokens = response.json()
                tokens["expires_at"] = (
                    datetime.now() + timedelta(seconds=tokens["expires_in"])
                ).isoformat()
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
        tokens["expires_at"] = (
            datetime.now() + timedelta(seconds=tokens["expires_in"])
        ).isoformat()
        save_json_file(TRAKT_TOKEN_FILE, tokens)
        print(f"[{timestamp()}] Token refreshed successfully!")
        return tokens

    @staticmethod
    def fetch_history(access_token, start_date):
        """Fetch viewing history from Trakt."""
        headers = {**TRAKT_API_HEADERS, "Authorization": f"Bearer {access_token}"}
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


# Toggl API
class TogglAPI:
    BASE_URL = "https://api.track.toggl.com/api/v9"

    @staticmethod
    def entry_exists(description, start_time, end_time):
        # Normalize timestamps to remove time zone differences
        def normalize_timestamp(timestamp):
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(
                microsecond=0
            )

        start_time = normalize_timestamp(start_time)
        end_time = normalize_timestamp(end_time)

        response = requests.get(
            f"{TogglAPI.BASE_URL}/me/time_entries",
            auth=(TOGGL_API_TOKEN, "api_token"),
        )
        response.raise_for_status()

        entries = response.json()

        # Check if there's an existing entry with the same description, start, stop, project_id, tags, and workspace_id
        for entry in entries:
            entry_start_time = normalize_timestamp(entry["start"])
            entry_end_time = normalize_timestamp(entry["stop"]) if entry["stop"] else None

            if (
                entry["description"] == description
                and entry_start_time == start_time
                and (entry_end_time == end_time if entry_end_time else False)
                and entry["project_id"] == TOGGL_PROJECT_ID
                and set(entry["tags"]) == set(TOGGL_TAGS)
                and entry["wid"] == TOGGL_WORKSPACE_ID
            ):
                return True
        return False

    @staticmethod
    def create_entry(description, start_time, end_time):
        if TogglAPI.entry_exists(description, start_time, end_time):
            print(f"[{timestamp()}] Entry '{description}' already exists. Skipping.")
            return

        data = {
            "description": description,
            "start": start_time,
            "stop": end_time,
            "created_with": "Trakt to Toggl Script",
            "project_id": TOGGL_PROJECT_ID,
            "tags": TOGGL_TAGS,
            "wid": TOGGL_WORKSPACE_ID,
        }
        response = requests.post(
            f"{TogglAPI.BASE_URL}/workspaces/{TOGGL_WORKSPACE_ID}/time_entries",
            json=data,
            auth=(TOGGL_API_TOKEN, "api_token"),
        )
        if response.status_code == 200:
            print(f"[{timestamp()}] Toggl entry created: {description}")
        else:
            print(
                f"[{timestamp()}] Failed to create Toggl entry: {response.text}",
                file=sys.stderr,
            )


# Main Process
def process_history_item(item):
    """Process a single history item."""
    watched_at = item["watched_at"]
    item_type = item["type"]

    if item_type == "episode":
        title = (
            f"📺 {item['show']['title']} - S{item['episode']['season']:02}E{item['episode']['number']:02} - "
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
    check_required_env_variables()  # Check environment variables

    tokens = load_json_file(TRAKT_TOKEN_FILE)
    if not tokens:
        tokens = TraktAPI.authenticate()
    elif TraktAPI.is_token_near_expiration(tokens["expires_at"]):
        tokens = TraktAPI.refresh_token(tokens["refresh_token"])

    print(f"Fetching Trakt history for the last {TRAKT_HISTORY_DAYS} days...")
    start_date = (datetime.now() - timedelta(days=TRAKT_HISTORY_DAYS)).isoformat() + "Z"
    history = TraktAPI.fetch_history(tokens["access_token"], start_date)

    for item in history:
        process_history_item(item)


if __name__ == "__main__":
    main()
