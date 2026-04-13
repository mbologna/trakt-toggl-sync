"""Toggl API client for time tracking."""

import sys
import time
from datetime import datetime, timedelta

import requests

from utils import timestamp


class TogglAPI:
    """Toggl API client for time tracking."""

    BASE_URL = "https://api.track.toggl.com/api/v9"
    DEFAULT_TIMEOUT = (3.05, 10)

    QUOTA_STOP_THRESHOLD = 3  # Stop proactively when this many requests remain

    def __init__(self, api_token, workspace_id, project_id, tags):
        self.api_token = api_token
        self.workspace_id = workspace_id
        self.project_id = project_id
        self.tags = tags
        self._cached_entries = None
        self._cache_timestamp = None
        self._cache_duration = 300  # Cache for 5 minutes
        self._rate_limited = False
        self._quota_remaining = None
        self._quota_resets_in = None

    def _update_quota(self, response):
        """Parse quota headers and proactively stop when running low."""
        remaining = response.headers.get("X-Toggl-Quota-Remaining")
        resets_in = response.headers.get("X-Toggl-Quota-Resets-In")
        if remaining is not None:
            self._quota_remaining = int(remaining)
        if resets_in is not None:
            self._quota_resets_in = int(resets_in)
        if (
            self._quota_remaining is not None
            and self._quota_remaining <= self.QUOTA_STOP_THRESHOLD
            and not self._rate_limited
        ):
            mins = round(self._quota_resets_in / 60) if self._quota_resets_in else "?"
            print(
                f"[{timestamp()}] ⚠ Toggl quota almost exhausted "
                f"({self._quota_remaining} requests remaining, resets in ~{mins} min). "
                f"Stopping to avoid hitting the limit."
            )
            self._rate_limited = True

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
        if self._rate_limited:
            return None

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
                    timeout=self.DEFAULT_TIMEOUT,
                )
                response.raise_for_status()
                self._rate_limited = False  # Clear any previous error state
                self._update_quota(response)  # May re-set if quota is low
                self._cached_entries = response.json()
                self._cache_timestamp = now
            except requests.exceptions.HTTPError as e:
                if e.response.status_code in (402, 429):
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

    def find_existing_entry(self, description, start_time, end_time):
        """Find an existing entry matching description+times. Returns entry dict or None."""
        entries = self.get_cached_entries()
        if entries is None:
            return None

        start_dt = self.normalize_timestamp(start_time)
        end_dt = self.normalize_timestamp(end_time)

        for entry in entries:
            if not entry.get("stop"):
                continue
            if (
                entry.get("description") == description
                and self.normalize_timestamp(entry["start"]) == start_dt
                and self.normalize_timestamp(entry["stop"]) == end_dt
                and entry.get("project_id") == self.project_id
                and set(entry.get("tags", [])) == set(self.tags)
                and entry.get("wid") == self.workspace_id
            ):
                return entry
        return None

    def entry_exists(self, description, start_time, end_time):
        """Check if entry exists using cached data."""
        if self.get_cached_entries() is None:
            return True  # Assume exists to avoid creating duplicates when rate limited
        return self.find_existing_entry(description, start_time, end_time) is not None

    def _is_entry_unchanged(self, entry_id, description, start_time, end_time):
        """Return True if the cached entry with entry_id already matches the given data."""
        if self._cached_entries is None:
            return False
        for entry in self._cached_entries:
            if entry.get("id") == entry_id:
                return (
                    entry.get("description") == description
                    and entry.get("stop") is not None
                    and self.normalize_timestamp(entry["start"]) == self.normalize_timestamp(start_time)
                    and self.normalize_timestamp(entry["stop"]) == self.normalize_timestamp(end_time)
                    and entry.get("project_id") == self.project_id
                    and set(entry.get("tags") or []) == set(self.tags)
                    and entry.get("wid") == self.workspace_id
                )
        return False

    def _update_cache_entry(self, entry):
        """Replace or append an entry in the local cache."""
        if self._cached_entries is None:
            return
        entry_id = entry.get("id")
        for i, e in enumerate(self._cached_entries):
            if e.get("id") == entry_id:
                self._cached_entries[i] = entry
                return
        self._cached_entries.append(entry)

    def _remove_from_cache(self, entry_id):
        """Remove an entry from the local cache by ID."""
        if self._cached_entries is not None:
            self._cached_entries = [e for e in self._cached_entries if e.get("id") != entry_id]

    def create_entry(self, description, start_time, end_time):
        """Create a new Toggl time entry. Returns the entry ID, or None on failure."""
        if self.get_cached_entries() is None:
            print(f"[{timestamp()}] Skipped (rate limited): {description}")
            return None

        existing = self.find_existing_entry(description, start_time, end_time)
        if existing:
            print(f"[{timestamp()}] Skipped (exists): {description}")
            return existing["id"]

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
                timeout=self.DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            self._update_quota(response)
            entry = response.json()
            start_dt = self.parse_time(start_time).strftime("%Y-%m-%d %H:%M")
            print(f"[{timestamp()}] ✓ Created: {description} (at {start_dt})")
            self._update_cache_entry(entry)
            return entry.get("id")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in (402, 429):
                print(f"[{timestamp()}] ⚠ Rate limit reached. Stopping sync.")
                self._rate_limited = True
                raise
            else:
                print(f"[{timestamp()}] ✗ Failed to create: {description} - {e.response.text}", file=sys.stderr)
                return None

    def update_entry(self, entry_id, description, start_time, end_time):
        """Update an existing Toggl time entry. Returns the entry ID, or None if not found."""
        if self._rate_limited:
            print(f"[{timestamp()}] Skipped (rate limited): {description}")
            return entry_id  # Entry still exists, we just can't update it right now

        if self._is_entry_unchanged(entry_id, description, start_time, end_time):
            return entry_id  # Nothing to do

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
            response = requests.put(
                f"{self.BASE_URL}/workspaces/{self.workspace_id}/time_entries/{entry_id}",
                json=data,
                auth=(self.api_token, "api_token"),
                timeout=self.DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            self._update_quota(response)
            updated = response.json()
            start_dt = self.parse_time(start_time).strftime("%Y-%m-%d %H:%M")
            print(f"[{timestamp()}] ↻ Updated: {description} (at {start_dt})")
            self._update_cache_entry(updated)
            return updated.get("id")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                self._remove_from_cache(entry_id)
                return None  # Entry was deleted from Toggl
            elif e.response.status_code in (402, 429):
                print(f"[{timestamp()}] ⚠ Rate limit reached. Stopping sync.")
                self._rate_limited = True
                raise
            else:
                print(f"[{timestamp()}] ✗ Failed to update: {description} - {e.response.text}", file=sys.stderr)
                return None

    def delete_entry(self, entry_id):
        """Delete a Toggl time entry by ID. Returns True on success."""
        try:
            response = requests.delete(
                f"{self.BASE_URL}/workspaces/{self.workspace_id}/time_entries/{entry_id}",
                auth=(self.api_token, "api_token"),
                timeout=self.DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                self._remove_from_cache(entry_id)
                return True
            return False
        except requests.exceptions.RequestException:
            return False

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
                        timeout=self.DEFAULT_TIMEOUT,
                    )
                    response.raise_for_status()
                    self._update_quota(response)
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code in (402, 429):
                        print(f"[{timestamp()}] ⚠ Toggl rate limit reached. Skipping deduplication.")
                        print(f"[{timestamp()}] This is temporary - try again in a few minutes.")
                        return
                    raise

                if self._rate_limited:
                    print(f"[{timestamp()}] ⚠ Quota too low to continue deduplication. Skipping.")
                    return

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

            # First pass: exact duplicates by (description, start, stop)
            entries_by_key = {}
            for entry in filtered_entries:
                desc = entry.get("description", "")
                if desc:
                    start = self.normalize_timestamp(entry["start"]).isoformat()
                    stop = self.normalize_timestamp(entry["stop"]).isoformat() if entry.get("stop") else ""
                    key = (desc, start, stop)
                    if key not in entries_by_key:
                        entries_by_key[key] = []
                    entries_by_key[key].append(entry)

            duplicates = {key: entries for key, entries in entries_by_key.items() if len(entries) > 1}

            first_pass_deleted_ids = set()
            if duplicates:
                total_deleted = 0
                entries_to_delete_count = sum(len(entries) - 1 for entries in duplicates.values())
                print(f"[{timestamp()}] Found {entries_to_delete_count} duplicate Toggl entries to remove:")

                for key, entries in duplicates.items():
                    print(f"  - {key[0]} ({len(entries)} occurrences)")
                    entries.sort(key=lambda x: x.get("id", 0))
                    entries_to_delete = entries[:-1]

                    for entry in entries_to_delete:
                        start = self.parse_time(entry["start"]).strftime("%Y-%m-%d %H:%M")
                        response = requests.delete(
                            f"{self.BASE_URL}/time_entries/{entry['id']}",
                            auth=(self.api_token, "api_token"),
                            timeout=self.DEFAULT_TIMEOUT,
                        )
                        if response.status_code == 200:
                            print(f"    ✓ Deleted: {start}")
                            total_deleted += 1
                            first_pass_deleted_ids.add(entry["id"])
                        else:
                            print(
                                f"    ✗ Failed to delete: {start} - {response.status_code}",
                                file=sys.stderr,
                            )

                print(f"[{timestamp()}] Successfully removed {total_deleted} duplicate Toggl entries")
                self._cached_entries = None
            else:
                print(f"[{timestamp()}] No exact Toggl duplicates found")

            # Second pass: close-in-time duplicates (same description, starts within 24h)
            # Handles re-watch entries created across separate sync runs
            CLOSE_WINDOW_SECONDS = 24 * 3600
            entries_by_desc: dict = {}
            for entry in filtered_entries:
                desc = entry.get("description", "")
                if desc:
                    entries_by_desc.setdefault(desc, []).append(entry)

            close_dups = []
            for desc_entries in entries_by_desc.values():
                if len(desc_entries) < 2:
                    continue
                desc_entries.sort(key=lambda x: x["start"])
                i = 0
                while i < len(desc_entries):
                    cluster = [desc_entries[i]]
                    j = i + 1
                    while j < len(desc_entries):
                        gap = (
                            self.normalize_timestamp(desc_entries[j]["start"])
                            - self.normalize_timestamp(desc_entries[j - 1]["start"])
                        ).total_seconds()
                        if gap <= CLOSE_WINDOW_SECONDS:
                            cluster.append(desc_entries[j])
                            j += 1
                        else:
                            break
                    if len(cluster) > 1:
                        cluster.sort(key=lambda x: x.get("id", 0))
                        close_dups.extend(cluster[:-1])
                    i = j

            # Exclude IDs already deleted in the first pass
            close_dups = [e for e in close_dups if e["id"] not in first_pass_deleted_ids]

            if close_dups:
                total_close_deleted = 0
                print(f"[{timestamp()}] Found {len(close_dups)} close-in-time Toggl duplicates to remove:")
                for entry in close_dups:
                    start = self.parse_time(entry["start"]).strftime("%Y-%m-%d %H:%M")
                    desc = entry.get("description", "")
                    response = requests.delete(
                        f"{self.BASE_URL}/time_entries/{entry['id']}",
                        auth=(self.api_token, "api_token"),
                        timeout=self.DEFAULT_TIMEOUT,
                    )
                    if response.status_code == 200:
                        print(f"  ✓ Deleted: {desc} (at {start})")
                        total_close_deleted += 1
                    else:
                        print(
                            f"  ✗ Failed to delete: {desc} (at {start}) - {response.status_code}",
                            file=sys.stderr,
                        )
                print(f"[{timestamp()}] Successfully removed {total_close_deleted} close-in-time Toggl duplicates")
                self._cached_entries = None
            else:
                print(f"[{timestamp()}] No close-in-time Toggl duplicates found")

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 402:
                print(f"[{timestamp()}] ⚠ Toggl rate limit reached. Skipping deduplication.")
                print(f"[{timestamp()}] This is temporary - try again in a few minutes.")
            else:
                raise
