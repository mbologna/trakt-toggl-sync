"""Trakt API client for managing viewing history."""

import os
import time
from datetime import datetime, timedelta

import requests

from utils import save_json_file, timestamp


class TraktAPI:
    """Trakt API client for managing viewing history."""

    BASE_URL = "https://api.trakt.tv"

    def __init__(self, client_id, client_secret, token_file):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_file = token_file
        self.token_expiration_buffer = 60  # minutes

    def _get_headers(self, access_token=None):
        """Get API headers with optional authorization."""
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self.client_id,
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    def is_token_near_expiration(self, expiration_time):
        """Check if token is near expiration."""
        now = datetime.now()
        expiration = datetime.fromisoformat(expiration_time)
        return now >= expiration - timedelta(minutes=self.token_expiration_buffer)

    def authenticate(self):
        """Authenticate with Trakt via device flow."""
        response = requests.post(
            f"{self.BASE_URL}/oauth/device/code",
            json={"client_id": self.client_id},
            headers=self._get_headers(),
        )
        response.raise_for_status()
        device_data = response.json()

        print(f"[{timestamp()}] Visit {device_data['verification_url']} and enter the code: {device_data['user_code']}")

        while True:
            time.sleep(device_data["interval"])
            response = requests.post(
                f"{self.BASE_URL}/oauth/device/token",
                json={"client_id": self.client_id, "code": device_data["device_code"]},
            )
            if response.status_code == 200:
                tokens = response.json()
                tokens["expires_at"] = (datetime.now() + timedelta(seconds=tokens["expires_in"])).isoformat()
                save_json_file(self.token_file, tokens)
                print(f"[{timestamp()}] Authentication successful!")
                return tokens
            elif response.status_code in {400, 404, 410, 418, 429}:
                print(f"[{timestamp()}] Waiting for user authentication...")
            else:
                print(f"[{timestamp()}] Authentication failed: {response.status_code}")
                break
        raise RuntimeError(f"[{timestamp()}] Authentication failed.")

    def refresh_token(self, refresh_token):
        """Refresh Trakt access token."""
        try:
            response = requests.post(
                f"{self.BASE_URL}/oauth/token",
                json={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            response.raise_for_status()
            tokens = response.json()
            tokens["expires_at"] = (datetime.now() + timedelta(seconds=tokens["expires_in"])).isoformat()
            save_json_file(self.token_file, tokens)
            print(f"[{timestamp()}] Token refreshed successfully!")
            return tokens
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                print(f"[{timestamp()}] Refresh token expired. Re-authenticating...")
                if os.path.exists(self.token_file):
                    os.remove(self.token_file)
                return self.authenticate()
            else:
                raise

    def fetch_full_history(self, access_token):
        """Fetch complete viewing history from Trakt."""
        headers = self._get_headers(access_token)
        history = []
        page = 1

        print(f"[{timestamp()}] Fetching complete Trakt history...")
        while True:
            response = requests.get(
                f"{self.BASE_URL}/sync/history",
                headers=headers,
                params={"page": page, "limit": 1000},
            )
            response.raise_for_status()
            data = response.json()
            if not data:
                break
            history.extend(data)
            page += 1

        print(f"[{timestamp()}] Fetched {len(history)} total Trakt history entries")
        return history

    def fetch_history(self, access_token, start_date):
        """Fetch viewing history from Trakt starting from a specific date."""
        headers = self._get_headers(access_token)
        history = []
        page = 1

        while True:
            response = requests.get(
                f"{self.BASE_URL}/sync/history?extended=full",
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

    def remove_duplicates(self, access_token):
        """Remove duplicate entries from Trakt history, keeping most recent."""
        print(f"[{timestamp()}] Starting Trakt deduplication...")
        history = self.fetch_full_history(access_token)

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
                desc = self.format_entry_description(dup)
                watched = dup["watched_at"][:10]
                print(f"  - {desc} (watched: {watched})")

            headers = self._get_headers(access_token)
            payload = {"ids": [entry["id"] for entry in duplicates]}
            response = requests.post(f"{self.BASE_URL}/sync/history/remove", headers=headers, json=payload)
            if response.status_code == 200:
                print(f"[{timestamp()}] ✓ Successfully removed {len(duplicates)} duplicate Trakt entries")
            else:
                print(f"[{timestamp()}] ✗ Failed to delete Trakt duplicates: {response.status_code}")
        else:
            print(f"[{timestamp()}] No Trakt duplicates found")
