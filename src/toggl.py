"""Toggl API client for time tracking."""

import sys
import time
from datetime import datetime, timedelta

import requests

from utils import timestamp


class TogglAPI:
    """Toggl API client for time tracking."""

    BASE_URL = "https://api.track.toggl.com/api/v9"

    def __init__(self, api_token, workspace_id, project_id, tags):
        self.api_token = api_token
        self.workspace_id = workspace_id
        self.project_id = project_id
        self.tags = tags
        self._cached_entries = None
        self._cache_timestamp = None
        self._cache_duration = 300  # Cache for 5 minutes
        self._rate_limited = False

    @staticmethod
    def parse_time(time_str):
        """Parse Toggl time strings to datetime."""
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"
        return datetime.fromisoformat(time_str)

    @staticmethod
    def normalize_timestamp(timestamp_str):
        """Normalize timestamps for comparison."""
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")).replace(microsecond=0)

    def get_cached_entries(self, start_date=None, force_refresh=False):
        """Get cached Toggl entries or fetch if cache is stale."""
        now = time.time()
        if (
            force_refresh
            or self._cached_entries is None
            or self._cache_timestamp is None
            or now - self._cache_timestamp > self._cache_duration
        ):
            try:
                # Fetch entries with date range if provided
                params = {}
                if start_date:
                    params["start_date"] = start_date
                    params["end_date"] = datetime.now().strftime("%Y-%m-%d")

                response = requests.get(
                    f"{self.BASE_URL}/me/time_entries",
                    params=params,
                    auth=(self.api_token, "api_token"),
                )
                response.raise_for_status()
                self._cached_entries = response.json()
                self._cache_timestamp = now
                self._rate_limited = False
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 402:
                    if not self._rate_limited:
                        print(f"[{timestamp()}] ⚠ Toggl rate limit reached.")
                        print(
                            f"[{timestamp()}] Cannot check for duplicates - will skip creating entries to avoid duplicates."
                        )
                        self._rate_limited = True
                    # Return None to signal rate limiting
                    return None
                else:
                    raise

        return self._cached_entries

    def entry_exists(self, description, start_time, end_time):
        """Check if entry exists using cached data."""
        entries = self.get_cached_entries()

        # If rate limited, we can't check, so return True to skip creation
        if entries is None:
            return True  # Assume exists to avoid creating duplicates

        start_time = self.normalize_timestamp(start_time)
        end_time = self.normalize_timestamp(end_time)

        for entry in entries:
            entry_start = self.normalize_timestamp(entry["start"])
            entry_end = self.normalize_timestamp(entry["stop"]) if entry.get("stop") else None

            if (
                entry["description"] == description
                and entry_start == start_time
                and (entry_end == end_time if entry_end else False)
                and entry.get("project_id") == self.project_id
                and set(entry.get("tags", [])) == set(self.tags)
                and entry.get("wid") == self.workspace_id
            ):
                return True
        return False

    def create_entry(self, description, start_time, end_time):
        """Create a new Toggl time entry."""
        if self.entry_exists(description, start_time, end_time):
            if self._rate_limited:
                print(f"[{timestamp()}] Skipped (rate limited): {description}")
            else:
                print(f"[{timestamp()}] Skipped (exists): {description}")
            return

        data = {
            "description": description,
            "start": start_time,
            "stop": end_time,
            "created_with": "trakt-toggl-sync",
            "project_id": self.project_id,
            "tags": self.tags,
            "wid": self.workspace_id,
        }

        try:
            response = requests.post(
                f"{self.BASE_URL}/workspaces/{self.workspace_id}/time_entries",
                json=data,
                auth=(self.api_token, "api_token"),
            )
            response.raise_for_status()
            start_dt = self.parse_time(start_time).strftime("%Y-%m-%d %H:%M")
            print(f"[{timestamp()}] ✓ Created: {description} (at {start_dt})")
            # Invalidate cache after creating entry
            self._cached_entries = None
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 402:
                print(f"[{timestamp()}] ⚠ Rate limit reached. Stopping sync.")
                self._rate_limited = True
                # Re-raise to stop the sync process
                raise
            else:
                print(f"[{timestamp()}] ✗ Failed to create: {description} - {e.response.text}", file=sys.stderr)

    def remove_duplicates(self):
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
                        f"{self.BASE_URL}/me/time_entries",
                        params=params,
                        auth=(self.api_token, "api_token"),
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

                project_entries = [e for e in batch if e.get("project_id") == self.project_id]
                all_entries.extend(project_entries)

                oldest_entry = min(batch, key=lambda x: x.get("start", ""))
                oldest_time = self.parse_time(oldest_entry["start"]).replace(tzinfo=None)

                if oldest_time < one_year_ago:
                    break

                current_before = (self.parse_time(oldest_entry["start"]) - timedelta(milliseconds=1)).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )

            # Filter to last year only
            filtered_entries = [
                e for e in all_entries if self.parse_time(e["start"]).replace(tzinfo=None) >= one_year_ago
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
                        start = self.parse_time(entry["start"]).strftime("%Y-%m-%d %H:%M")
                        response = requests.delete(
                            f"{self.BASE_URL}/time_entries/{entry['id']}",
                            auth=(self.api_token, "api_token"),
                        )
                        if response.status_code == 200:
                            print(f"    ✓ Deleted: {start}")
                            total_deleted += 1
                        else:
                            print(
                                f"    ✗ Failed to delete: {start} - {response.status_code}",
                                file=sys.stderr,
                            )

                print(f"[{timestamp()}] Successfully removed {total_deleted} duplicate Toggl entries")
                # Clear cache after deletion
                self._cached_entries = None
            else:
                print(f"[{timestamp()}] No Toggl duplicates found")

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 402:
                print(f"[{timestamp()}] ⚠ Toggl rate limit reached. Skipping deduplication.")
                print(f"[{timestamp()}] This is temporary - try again in a few minutes.")
            else:
                raise
