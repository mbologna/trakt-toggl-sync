"""End-to-end tests for trakt-toggl-sync.

These tests use real API credentials stored in environment variables.
They are only run when E2E_TEST_ENABLED=true.

Setup Instructions:
1. Create a Trakt API app at https://trakt.tv/oauth/applications
2. Run the sync locally once to authenticate: `make run`
3. This creates .trakt_tokens.json with valid tokens
4. Set environment variables from that file:
   export E2E_TRAKT_CLIENT_ID="your_client_id"
   export E2E_TRAKT_CLIENT_SECRET="your_client_secret"
   export E2E_TRAKT_ACCESS_TOKEN="$(jq -r .access_token .trakt_tokens.json)"
   export E2E_TRAKT_REFRESH_TOKEN="$(jq -r .refresh_token .trakt_tokens.json)"
5. Set Toggl credentials:
   export E2E_TOGGL_API_TOKEN="your_token"
   export E2E_TOGGL_WORKSPACE_ID="123"
   export E2E_TOGGL_PROJECT_ID="456"
6. Enable E2E tests:
   export E2E_TEST_ENABLED=true
7. Run tests:
   make test-e2e

Note: Token refresh test may require manual authentication if tokens are expired.
"""

import os
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest
import requests

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from toggl import TogglAPI
from trakt import TraktAPI

# Skip all E2E tests if not explicitly enabled
pytestmark = pytest.mark.skipif(
    os.getenv("E2E_TEST_ENABLED") != "true",
    reason="E2E tests only run when E2E_TEST_ENABLED=true",
)


@pytest.fixture(scope="session")
def trakt_credentials():
    """Get Trakt credentials from environment."""
    client_id = os.getenv("E2E_TRAKT_CLIENT_ID")
    client_secret = os.getenv("E2E_TRAKT_CLIENT_SECRET")

    if not client_id or not client_secret:
        pytest.skip("E2E Trakt credentials not configured")

    return {"client_id": client_id, "client_secret": client_secret}


@pytest.fixture(scope="session")
def trakt_api(trakt_credentials):
    """Create TraktAPI client with test credentials."""
    return TraktAPI(
        trakt_credentials["client_id"],
        trakt_credentials["client_secret"],
        "/tmp/e2e_trakt_tokens.json",
    )


@pytest.fixture(scope="session")
def valid_trakt_token(trakt_api, trakt_credentials):
    """Get or create valid Trakt access token.

    This fixture tries to use provided tokens, and if they fail,
    attempts to refresh them. If refresh fails, tests requiring
    this fixture will be skipped.
    """
    access_token = os.getenv("E2E_TRAKT_ACCESS_TOKEN")
    refresh_token = os.getenv("E2E_TRAKT_REFRESH_TOKEN")

    if not access_token or not refresh_token:
        pytest.skip("E2E Trakt tokens not configured. Run 'make run' first to authenticate.")

    # Try the access token first
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": trakt_credentials["client_id"],
        "Authorization": f"Bearer {access_token}",
    }

    # Quick validation check
    try:
        response = requests.get("https://api.trakt.tv/sync/history", headers=headers, params={"limit": 1})
        if response.status_code == 200:
            print("\n✓ Using existing access token")
            return access_token
        elif response.status_code == 401:
            print("\n⚠ Access token expired, attempting refresh...")
    except Exception as e:
        print(f"\n⚠ Error checking token: {e}")

    # Try to refresh
    try:
        new_tokens = trakt_api.refresh_token(refresh_token)
        print("✓ Token refreshed successfully")
        return new_tokens["access_token"]
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            pytest.skip(
                "Trakt tokens expired and refresh failed. "
                "Run 'make run' locally to re-authenticate, then update E2E_TRAKT_* env vars."
            )
        raise


@pytest.fixture(scope="session")
def toggl_api():
    """Create TogglAPI client with test credentials."""
    api_token = os.getenv("E2E_TOGGL_API_TOKEN")
    workspace_id = int(os.getenv("E2E_TOGGL_WORKSPACE_ID", "0"))
    project_id = int(os.getenv("E2E_TOGGL_PROJECT_ID", "0"))

    if not api_token or not workspace_id or not project_id:
        pytest.skip("E2E Toggl credentials not configured")

    return TogglAPI(api_token, workspace_id, project_id, ["e2e-test"])


class TestTraktE2E:
    """End-to-end tests for Trakt API."""

    def test_fetch_recent_history(self, trakt_api, valid_trakt_token):
        """Test fetching recent history from Trakt."""
        # Fetch last 7 days
        start_date = (datetime.now() - timedelta(days=7)).isoformat() + "Z"
        history = trakt_api.fetch_history(valid_trakt_token, start_date)

        # Should return a list (might be empty if no recent watches)
        assert isinstance(history, list)
        print(f"\n✓ Fetched {len(history)} entries from Trakt (last 7 days)")

        # If we have history, verify structure
        if history:
            item = history[0]
            assert "type" in item
            assert item["type"] in ["movie", "episode"]
            assert "watched_at" in item

            if item["type"] == "movie":
                assert "movie" in item
                assert "title" in item["movie"]
            else:
                assert "show" in item
                assert "episode" in item
                assert "title" in item["show"]

            print(f"  Sample: {TraktAPI.format_entry_description(item)}")

    def test_fetch_full_history(self, trakt_api, valid_trakt_token):
        """Test fetching complete viewing history."""
        history = trakt_api.fetch_full_history(valid_trakt_token)

        assert isinstance(history, list)
        print(f"\n✓ Fetched {len(history)} total entries from Trakt")

        # Should have at least some history
        assert len(history) > 0, "Expected some viewing history"

    def test_format_entry(self):
        """Test formatting various entry types."""
        # Movie entry
        movie_entry = {
            "type": "movie",
            "movie": {"title": "Test Movie", "year": 2024},
        }
        result = TraktAPI.format_entry_description(movie_entry)
        assert "Test Movie" in result
        assert "2024" in result
        assert "🎞️" in result

        # Episode entry
        episode_entry = {
            "type": "episode",
            "show": {"title": "Test Show"},
            "episode": {"season": 1, "number": 1},
        }
        result = TraktAPI.format_entry_description(episode_entry)
        assert "Test Show" in result
        assert "S01E01" in result
        assert "📺" in result

    def test_remove_duplicates(self, trakt_api, valid_trakt_token):
        """Test deduplication (should run without errors)."""
        trakt_api.remove_duplicates(valid_trakt_token)
        print("\n✓ Trakt deduplication completed successfully")


class TestTogglE2E:
    """End-to-end tests for Toggl API."""

    def test_get_entries(self, toggl_api):
        """Test fetching Toggl entries."""
        entries = toggl_api.get_cached_entries()

        # Should get entries or None if rate limited
        if toggl_api._rate_limited:
            pytest.skip("Toggl API rate limited")

        assert entries is not None
        assert isinstance(entries, list)
        print(f"\n✓ Fetched {len(entries)} Toggl entries")

    def test_get_entries_with_date_range(self, toggl_api):
        """Test fetching Toggl entries with date range."""
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        entries = toggl_api.get_cached_entries(start_date=start_date, force_refresh=True)

        if toggl_api._rate_limited:
            pytest.skip("Toggl API rate limited")

        assert entries is not None
        assert isinstance(entries, list)
        print(f"\n✓ Fetched {len(entries)} entries from last 7 days")

        # Verify entries are within date range if any exist
        if entries:
            cutoff_date = datetime.now(UTC) - timedelta(days=8)
            for entry in entries:
                entry_date = toggl_api.parse_time(entry["start"])
                assert entry_date >= cutoff_date

    def test_create_and_verify_entry(self, toggl_api):
        """Test creating a time entry and verifying it exists."""
        # Create a unique test entry
        timestamp_suffix = int(datetime.now().timestamp())
        description = f"E2E Test {timestamp_suffix}"
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=15)

        start_iso = start_time.isoformat() + "Z"
        end_iso = end_time.isoformat() + "Z"

        # Should not exist initially
        exists_before = toggl_api.entry_exists(description, start_iso, end_iso)
        assert not exists_before, "Entry should not exist before creation"

        # Create entry
        toggl_api.create_entry(description, start_iso, end_iso)

        if toggl_api._rate_limited:
            pytest.skip("Toggl API rate limited during test")

        print(f"\n✓ Created test entry: {description}")

        # Verify it exists (force cache refresh)
        time.sleep(2)  # Brief wait for API consistency
        toggl_api.get_cached_entries(force_refresh=True)
        exists_after = toggl_api.entry_exists(description, start_iso, end_iso)
        assert exists_after, "Entry should exist after creation"

        print("✓ Entry verified in Toggl")
        print("⚠ Clean up test entries manually or they'll accumulate")

    def test_duplicate_prevention(self, toggl_api):
        """Test that duplicate detection prevents creating the same entry twice."""
        timestamp_suffix = int(datetime.now().timestamp())
        description = f"E2E Dup Test {timestamp_suffix}"
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=10)

        start_iso = start_time.isoformat() + "Z"
        end_iso = end_time.isoformat() + "Z"

        # Create entry first time
        toggl_api.create_entry(description, start_iso, end_iso)

        if toggl_api._rate_limited:
            pytest.skip("Toggl API rate limited during test")

        time.sleep(2)

        # Try to create again - should be skipped
        initial_count = len(toggl_api.get_cached_entries(force_refresh=True) or [])
        toggl_api.create_entry(description, start_iso, end_iso)
        final_count = len(toggl_api.get_cached_entries(force_refresh=True) or [])

        # Count should be the same (no duplicate created)
        assert initial_count == final_count, "Duplicate should have been prevented"
        print("\n✓ Duplicate detection working correctly")

    def test_remove_duplicates(self, toggl_api):
        """Test deduplication (should run without errors)."""
        toggl_api.remove_duplicates()

        if toggl_api._rate_limited:
            pytest.skip("Toggl API rate limited during deduplication")

        print("\n✓ Toggl deduplication completed successfully")


class TestIntegrationE2E:
    """Full integration tests."""

    def test_sync_workflow(self, trakt_api, valid_trakt_token, toggl_api):
        """Test a simplified sync workflow."""
        # 1. Fetch recent Trakt history
        start_date = (datetime.now() - timedelta(days=2)).isoformat() + "Z"
        history = trakt_api.fetch_history(valid_trakt_token, start_date)

        print(f"\n✓ Fetched {len(history)} entries from Trakt (last 2 days)")

        if not history:
            pytest.skip("No recent Trakt history to test with")

        # 2. Pre-fetch Toggl entries for the same period
        start_date_str = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        toggl_entries = toggl_api.get_cached_entries(start_date=start_date_str, force_refresh=True)

        if toggl_api._rate_limited:
            pytest.skip("Toggl API rate limited")

        print(f"✓ Fetched {len(toggl_entries or [])} Toggl entries")

        # 3. Check first few entries
        tested = 0
        for item in history[:5]:  # Test only first 5 to avoid rate limits
            if item["type"] == "episode":
                title = (
                    f"📺 {item['show']['title']} - "
                    f"S{item['episode']['season']:02}E{item['episode']['number']:02} - "
                    f"{item['episode']['title']}"
                )
                runtime = item["episode"]["runtime"]
            else:
                title = f"🎞️ {item['movie']['title']} ({item['movie'].get('year', 'N/A')})"
                runtime = item["movie"].get("runtime", 0)

            watched_at = item["watched_at"]
            end_time = datetime.fromisoformat(watched_at[:-1])
            start_time = end_time - timedelta(minutes=runtime)

            exists = toggl_api.entry_exists(
                title,
                start_time.isoformat() + "Z",
                watched_at,
            )

            status = "exists" if exists else "would be created"
            print(f"  {title[:60]}: {status}")
            tested += 1

        print(f"\n✓ Checked {tested} entries successfully")

    def test_deduplication_workflow(self, trakt_api, valid_trakt_token, toggl_api):
        """Test the complete deduplication workflow."""
        print("\n=== Testing Deduplication Workflow ===")

        # Deduplicate Trakt
        print("\n1. Deduplicating Trakt...")
        trakt_api.remove_duplicates(valid_trakt_token)

        # Deduplicate Toggl
        print("\n2. Deduplicating Toggl...")
        toggl_api.remove_duplicates()

        if toggl_api._rate_limited:
            pytest.skip("Toggl API rate limited during deduplication")

        print("\n✓ Deduplication workflow completed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
