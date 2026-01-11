import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

# Add parent directory to path to import sync module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import sync


class TestUtilityFunctions:
    """Test utility functions."""

    def test_timestamp_format(self):
        """Test timestamp returns correct format."""
        ts = sync.timestamp()
        # Should match format: YYYY-MM-DD HH:MM:SS
        assert len(ts) == 19
        assert ts[4] == "-"
        assert ts[7] == "-"
        assert ts[10] == " "
        assert ts[13] == ":"
        assert ts[16] == ":"

    def test_load_json_file_exists(self, tmp_path):
        """Test loading existing JSON file."""
        test_file = tmp_path / "test.json"
        test_data = {"key": "value"}
        test_file.write_text(json.dumps(test_data))

        result = sync.load_json_file(str(test_file))
        assert result == test_data

    def test_load_json_file_not_exists(self):
        """Test loading non-existent JSON file returns None."""
        result = sync.load_json_file("nonexistent.json")
        assert result is None

    def test_save_json_file(self, tmp_path):
        """Test saving JSON file with correct permissions."""
        test_file = tmp_path / "test.json"
        test_data = {"key": "value"}

        sync.save_json_file(str(test_file), test_data)

        # Check file exists and has correct content
        assert test_file.exists()
        assert json.loads(test_file.read_text()) == test_data

        # Check permissions (600 = owner read/write only)
        stat = os.stat(test_file)
        assert oct(stat.st_mode)[-3:] == "600"

    @patch.dict(os.environ, {}, clear=True)
    def test_check_required_env_variables_missing(self):
        """Test check_required_env_variables exits when vars are missing."""
        with pytest.raises(SystemExit) as exc_info:
            sync.check_required_env_variables()
        assert exc_info.value.code == 1

    @patch.dict(
        os.environ,
        {
            "TRAKT_CLIENT_ID": "test",
            "TRAKT_CLIENT_SECRET": "test",
            "TOGGL_API_TOKEN": "test",
            "TOGGL_WORKSPACE_ID": "123",
            "TOGGL_PROJECT_ID": "456",
        },
    )
    def test_check_required_env_variables_success(self):
        """Test check_required_env_variables passes with all vars set."""
        # Should not raise
        sync.check_required_env_variables()


class TestTraktAPI:
    """Test Trakt API methods."""

    def test_is_token_near_expiration_expired(self):
        """Test token expiration check for expired token."""
        expired_time = (datetime.now() - timedelta(hours=1)).isoformat()
        assert sync.TraktAPI.is_token_near_expiration(expired_time) is True

    def test_is_token_near_expiration_valid(self):
        """Test token expiration check for valid token."""
        future_time = (datetime.now() + timedelta(hours=2)).isoformat()
        assert sync.TraktAPI.is_token_near_expiration(future_time) is False

    def test_get_headers(self):
        """Test get_headers returns correct format."""
        token = "test_token"
        headers = sync.TraktAPI.get_headers(token)

        assert headers["Authorization"] == f"Bearer {token}"
        assert headers["Content-Type"] == "application/json"
        assert headers["trakt-api-version"] == "2"

    def test_format_entry_description_movie(self):
        """Test formatting movie entry description."""
        entry = {"type": "movie", "movie": {"title": "The Matrix", "year": 1999}}
        result = sync.TraktAPI.format_entry_description(entry)
        assert result == "🎞️ The Matrix (1999)"

    def test_format_entry_description_episode(self):
        """Test formatting episode entry description."""
        entry = {"type": "episode", "show": {"title": "Breaking Bad"}, "episode": {"season": 1, "number": 1}}
        result = sync.TraktAPI.format_entry_description(entry)
        assert result == "📺 Breaking Bad - S01E01"

    @patch("sync.requests.post")
    @patch("sync.save_json_file")
    def test_refresh_token_success(self, mock_save, mock_post):
        """Test successful token refresh."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "new_token", "expires_in": 7200}
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {"TRAKT_CLIENT_ID": "test_id", "TRAKT_CLIENT_SECRET": "test_secret"}):
            result = sync.TraktAPI.refresh_token("old_refresh_token")

        assert result["access_token"] == "new_token"
        assert "expires_at" in result
        mock_save.assert_called_once()

    @patch("sync.requests.get")
    def test_fetch_history(self, mock_get):
        """Test fetching history from Trakt."""
        # Mock first page with data, second page empty
        mock_response1 = Mock()
        mock_response1.status_code = 200
        mock_response1.json.return_value = [{"id": 1, "type": "movie"}, {"id": 2, "type": "episode"}]

        mock_response2 = Mock()
        mock_response2.status_code = 200
        mock_response2.json.return_value = []

        mock_get.side_effect = [mock_response1, mock_response2]

        start_date = "2025-01-01T00:00:00Z"
        result = sync.TraktAPI.fetch_history("test_token", start_date)

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2


class TestTogglAPI:
    """Test Toggl API methods."""

    def test_parse_time_with_z(self):
        """Test parsing time string with Z suffix."""
        time_str = "2025-01-01T12:00:00Z"
        result = sync.TogglAPI.parse_time(time_str)
        assert isinstance(result, datetime)
        assert result.year == 2025
        assert result.month == 1
        assert result.day == 1

    def test_normalize_timestamp(self):
        """Test timestamp normalization."""
        timestamp = "2025-01-01T12:00:00.123456Z"
        result = sync.TogglAPI.normalize_timestamp(timestamp)

        assert result.microsecond == 0
        assert result.hour == 12

    @patch("sync.requests.get")
    def test_get_cached_entries_first_call(self, mock_get):
        """Test get_cached_entries fetches on first call."""
        # Reset cache
        sync.TogglAPI._cached_entries = None
        sync.TogglAPI._cache_timestamp = None

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": 1}, {"id": 2}]
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {"TOGGL_API_TOKEN": "test_token"}):
            result = sync.TogglAPI.get_cached_entries()

        assert len(result) == 2
        assert sync.TogglAPI._cached_entries is not None
        mock_get.assert_called_once()

    @patch("sync.requests.get")
    def test_get_cached_entries_uses_cache(self, mock_get):
        """Test get_cached_entries uses cache on subsequent calls."""
        # Set up cache
        import time

        sync.TogglAPI._cached_entries = [{"id": 1}]
        sync.TogglAPI._cache_timestamp = time.time()

        with patch.dict(os.environ, {"TOGGL_API_TOKEN": "test_token"}):
            result = sync.TogglAPI.get_cached_entries()

        assert len(result) == 1
        mock_get.assert_not_called()

    def test_entry_exists_true(self):
        """Test entry_exists returns True for matching entry."""
        sync.TogglAPI._cached_entries = [
            {
                "description": "Test Entry",
                "start": "2025-01-01T12:00:00Z",
                "stop": "2025-01-01T13:00:00Z",
                "project_id": 123,
                "tags": ["test"],
                "wid": 456,
            }
        ]

        with patch.dict(
            os.environ,
            {"TOGGL_API_TOKEN": "test", "TOGGL_PROJECT_ID": "123", "TOGGL_WORKSPACE_ID": "456", "TOGGL_TAGS": "test"},
        ):
            sync.TOGGL_PROJECT_ID = 123
            sync.TOGGL_WORKSPACE_ID = 456
            sync.TOGGL_TAGS = ["test"]

            result = sync.TogglAPI.entry_exists("Test Entry", "2025-01-01T12:00:00Z", "2025-01-01T13:00:00Z")

        assert result is True

    def test_entry_exists_false(self):
        """Test entry_exists returns False for non-matching entry."""
        sync.TogglAPI._cached_entries = [
            {
                "description": "Other Entry",
                "start": "2025-01-01T12:00:00Z",
                "stop": "2025-01-01T13:00:00Z",
                "project_id": 123,
                "tags": [],
                "wid": 456,
            }
        ]

        with patch.dict(
            os.environ,
            {"TOGGL_API_TOKEN": "test", "TOGGL_PROJECT_ID": "123", "TOGGL_WORKSPACE_ID": "456", "TOGGL_TAGS": ""},
        ):
            sync.TOGGL_PROJECT_ID = 123
            sync.TOGGL_WORKSPACE_ID = 456
            sync.TOGGL_TAGS = []

            result = sync.TogglAPI.entry_exists("Test Entry", "2025-01-01T12:00:00Z", "2025-01-01T13:00:00Z")

        assert result is False

    @patch("sync.TogglAPI.entry_exists")
    @patch("sync.requests.post")
    def test_create_entry_success(self, mock_post, mock_exists):
        """Test successful entry creation."""
        mock_exists.return_value = False
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        with patch.dict(
            os.environ,
            {"TOGGL_API_TOKEN": "test", "TOGGL_WORKSPACE_ID": "456", "TOGGL_PROJECT_ID": "123", "TOGGL_TAGS": "test"},
        ):
            sync.TOGGL_WORKSPACE_ID = 456
            sync.TOGGL_PROJECT_ID = 123
            sync.TOGGL_TAGS = ["test"]

            sync.TogglAPI.create_entry("Test Entry", "2025-01-01T12:00:00Z", "2025-01-01T13:00:00Z")

        mock_post.assert_called_once()

    @patch("sync.TogglAPI.entry_exists")
    def test_create_entry_skips_existing(self, mock_exists):
        """Test create_entry skips when entry exists."""
        mock_exists.return_value = True

        with patch("sync.requests.post") as mock_post:
            sync.TogglAPI.create_entry("Test Entry", "2025-01-01T12:00:00Z", "2025-01-01T13:00:00Z")

        mock_post.assert_not_called()


class TestProcessHistoryItem:
    """Test process_history_item function."""

    @patch("sync.TogglAPI.create_entry")
    def test_process_movie(self, mock_create):
        """Test processing a movie history item."""
        item = {
            "type": "movie",
            "watched_at": "2025-01-01T14:00:00Z",
            "movie": {"title": "The Matrix", "year": 1999, "runtime": 120},
        }

        sync.process_history_item(item)

        mock_create.assert_called_once()
        args = mock_create.call_args[1]
        assert "The Matrix" in args["description"]
        assert "1999" in args["description"]

    @patch("sync.TogglAPI.create_entry")
    def test_process_episode(self, mock_create):
        """Test processing an episode history item."""
        item = {
            "type": "episode",
            "watched_at": "2025-01-01T14:00:00Z",
            "show": {"title": "Breaking Bad"},
            "episode": {"season": 1, "number": 1, "title": "Pilot", "runtime": 45},
        }

        sync.process_history_item(item)

        mock_create.assert_called_once()
        args = mock_create.call_args[1]
        assert "Breaking Bad" in args["description"]
        assert "S01E01" in args["description"]
        assert "Pilot" in args["description"]


class TestIntegration:
    """Integration tests."""

    @patch("sync.TraktAPI.remove_duplicates")
    @patch("sync.TogglAPI.remove_duplicates")
    @patch("sync.TraktAPI.fetch_history")
    @patch("sync.process_history_item")
    @patch("sync.load_json_file")
    @patch("sync.check_required_env_variables")
    def test_main_flow(self, mock_check, mock_load, mock_process, mock_fetch, mock_toggl_dedup, mock_trakt_dedup):
        """Test main function flow."""
        # Mock valid token
        mock_load.return_value = {
            "access_token": "test_token",
            "expires_at": (datetime.now() + timedelta(hours=2)).isoformat(),
        }

        # Mock history data
        mock_fetch.return_value = [
            {
                "type": "movie",
                "watched_at": "2025-01-01T14:00:00Z",
                "movie": {"title": "Test Movie", "year": 2025, "runtime": 90},
            }
        ]

        with patch.dict(os.environ, {"TRAKT_HISTORY_DAYS": "7"}):
            sync.main()

        # Verify all steps were called
        mock_check.assert_called_once()
        mock_trakt_dedup.assert_called_once()
        mock_toggl_dedup.assert_called_once()
        mock_fetch.assert_called_once()
        mock_process.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
