"""Utility functions for trakt-toggl-sync."""

import json
import os
import sys
from datetime import datetime


def timestamp():
    """Generate current timestamp for logging."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json_file(file_path):
    """Load JSON data from file."""
    if os.path.exists(file_path):
        try:
            with open(file_path) as f:
                content = f.read().strip()
                if not content:  # Empty file
                    return None
                return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            # Invalid or empty JSON file
            print(f"[{timestamp()}] Warning: Invalid token file, will re-authenticate")
            return None
    return None


def save_json_file(file_path, data):
    """Save JSON data to file with restricted permissions."""
    # Ensure directory exists
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(file_path, 0o600)


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
