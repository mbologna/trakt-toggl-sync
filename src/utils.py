"""Utility functions for trakt-toggl-sync."""

import json
import os
import sys
from datetime import datetime


def timestamp():
    """Generate current timestamp for logging."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_gcs_path(path):
    """Parse a gs://bucket/blob path into (bucket_name, blob_name)."""
    without_prefix = path[5:]  # strip "gs://"
    bucket, _, blob = without_prefix.partition("/")
    return bucket, blob


def load_json_file(file_path):
    """Load JSON data from a local file or a GCS object (gs://)."""
    if file_path.startswith("gs://"):
        from google.cloud import storage

        bucket_name, blob_name = _parse_gcs_path(file_path)
        client = storage.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        if not blob.exists():
            return None
        content = blob.download_as_text().strip()
        if not content:
            return None
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            print(f"[{timestamp()}] Warning: Invalid JSON in {file_path}, will re-authenticate")
            return None

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
    """Save JSON data to a local file (mode 0o600) or a GCS object (gs://)."""
    if file_path.startswith("gs://"):
        from google.cloud import storage

        bucket_name, blob_name = _parse_gcs_path(file_path)
        client = storage.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")
        return

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
