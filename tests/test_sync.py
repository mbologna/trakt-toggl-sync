import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

import utils
from toggl import TogglAPI
from trakt import TraktAPI


class TestUtilityFunctions:
    """Test utility functions."""

    def test_timestamp_format(self):
        """Test timestamp returns correct format."""
        ts = utils.timestamp()
        assert len(ts) == 19
        assert ts[4] == "-"
        assert ts[10] == " "

    def test_load_json_file_exists(self, tmp_path):
        """Test loading existing JSON file."""
        test_file = tmp_path / "test.json"
        test_data = {"key": "value"}
        test_file.write_text(json.dumps(test_data))

        result = utils.load_json_file(str(test_file))
        assert result == test_data

    def test_load_json_file_not_exists(self):
        """Test loading non-existent JSON file returns None."""
        result = utils.load_json_file("nonexistent.json")
        assert result is None

    def test_load_json_file_empty(self, tmp_path):
        """Test loading empty JSON file returns None."""
        test_file = tmp_path / "empty.json"
        test_file.write_text("")

        result = utils.load_json_file(str(test_file))
        assert result is None

    def test_save_json_file(self, tmp_path):
        """Test saving JSON file with correct permissions."""
        test_file = tmp_path / "test.json"
        test_data = {"key": "value"}

        utils.save_json_file(str(test_file), test_data)

        assert test_file.exists()
        assert json.loads(test_file.read_text()) == test_data
        stat = os.stat(test_file)
        assert oct(stat.st_mode)[-3:] == "600"


class TestTraktAPI:
    """Test Trakt API methods."""

    def test_is_token_near_expiration_expired(self):
        """Test token expiration check for expired token."""
        api = TraktAPI("client_id", "client_secret", "token.json")
        expired_time = (datetime.now() - timedelta(hours=1)).isoformat()
        assert api.is_token_near_expiration(expired_time) is True

    def test_is_token_near_expiration_valid(self):
        """Test token expiration check for valid token."""
        api = TraktAPI("client_id", "client_secret", "token.json")
        future_time = (datetime.now() + timedelta(hours=2)).isoformat()
        assert api.is_token_near_expiration(future_time) is False

    def test_format_entry_description_movie(self):
        """Test formatting movie entry description."""
        entry = {"type": "movie", "movie": {"title": "The Matrix", "year": 1999}}
        result = TraktAPI.format_entry_description(entry)
        assert result == "🎞️ The Matrix (1999)"

    def test_format_entry_description_episode(self):
        """Test formatting episode entry description."""
        entry = {"type": "episode", "show": {"title": "Breaking Bad"}, "episode": {"season": 1, "number": 1}}
        result = TraktAPI.format_entry_description(entry)
        assert result == "📺 Breaking Bad - S01E01"


class TestTogglAPI:
    """Test Toggl API methods."""

    def test_parse_time_with_z(self):
        """Test parsing time string with Z suffix."""
        time_str = "2025-01-01T12:00:00Z"
        result = TogglAPI.parse_time(time_str)
        assert isinstance(result, datetime)
        assert result.year == 2025

    def test_normalize_timestamp(self):
        """Test timestamp normalization."""
        timestamp = "2025-01-01T12:00:00.123456Z"
        result = TogglAPI.normalize_timestamp(timestamp)
        assert result.microsecond == 0

    def test_entry_exists_with_rate_limit(self):
        """Test entry_exists returns True when rate limited."""
        api = TogglAPI("token", 123, 456, ["tag"])

        # Mock the get_cached_entries to return None (simulating rate limit)
        with patch.object(api, "get_cached_entries", return_value=None):
            result = api.entry_exists("Test", "2025-01-01T12:00:00Z", "2025-01-01T13:00:00Z")
            assert result is True

    def test_get_cached_entries_skips_when_rate_limited(self):
        """Test get_cached_entries returns None immediately when rate limited, without making a network call."""
        api = TogglAPI("token", 123, 456, ["tag"])
        api._rate_limited = True

        with patch("toggl.requests.get") as mock_get:
            result = api.get_cached_entries()
            assert result is None
            mock_get.assert_not_called()

    def test_update_entry_skips_when_rate_limited(self):
        """Test update_entry returns the entry_id and skips the network call when rate limited."""
        api = TogglAPI("token", 123, 456, ["tag"])
        api._rate_limited = True

        with patch("toggl.requests.put") as mock_put:
            result = api.update_entry(999, "Test", "2025-01-01T12:00:00Z", "2025-01-01T13:00:00Z")
            assert result == 999
            mock_put.assert_not_called()

    def test_create_entry_skips_when_rate_limited(self):
        """Test create_entry returns None and skips the network call when rate limited."""
        api = TogglAPI("token", 123, 456, ["tag"])
        api._rate_limited = True

        with patch("toggl.requests.post") as mock_post:
            result = api.create_entry("Test", "2025-01-01T12:00:00Z", "2025-01-01T13:00:00Z")
            assert result is None
            mock_post.assert_not_called()

    def test_update_quota_sets_rate_limited_when_low(self):
        """Test _update_quota proactively sets _rate_limited when quota hits the threshold."""
        api = TogglAPI("token", 123, 456, ["tag"])

        mock_response = MagicMock()
        mock_response.headers = {
            "X-Toggl-Quota-Remaining": str(TogglAPI.QUOTA_STOP_THRESHOLD),
            "X-Toggl-Quota-Resets-In": "1800",
        }
        api._update_quota(mock_response)

        assert api._rate_limited is True
        assert api._quota_remaining == TogglAPI.QUOTA_STOP_THRESHOLD
        assert api._quota_resets_in == 1800

    def test_update_quota_does_not_set_rate_limited_when_ample(self):
        """Test _update_quota does not set _rate_limited when quota is sufficient."""
        api = TogglAPI("token", 123, 456, ["tag"])

        mock_response = MagicMock()
        mock_response.headers = {
            "X-Toggl-Quota-Remaining": "20",
            "X-Toggl-Quota-Resets-In": "1800",
        }
        api._update_quota(mock_response)

        assert api._rate_limited is False
        assert api._quota_remaining == 20

    def test_update_quota_no_headers(self):
        """Test _update_quota handles missing quota headers gracefully."""
        api = TogglAPI("token", 123, 456, ["tag"])

        mock_response = MagicMock()
        mock_response.headers = {}
        api._update_quota(mock_response)

        assert api._rate_limited is False
        assert api._quota_remaining is None

    def test_get_cached_entries_proactive_rate_limit(self):
        """Test get_cached_entries sets rate limited after quota threshold response."""
        api = TogglAPI("token", 123, 456, ["tag"])

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status.return_value = None
        mock_response.headers = {
            "X-Toggl-Quota-Remaining": "2",
            "X-Toggl-Quota-Resets-In": "600",
        }

        with patch("toggl.requests.get", return_value=mock_response):
            result = api.get_cached_entries()

        # Request succeeded but quota is now too low — next call should be blocked
        assert result == []
        assert api._rate_limited is True

    def test_get_cached_entries_handles_429(self):
        """Test get_cached_entries handles 429 rate limit response."""
        api = TogglAPI("token", 123, 456, ["tag"])

        mock_response = MagicMock()
        mock_response.status_code = 429
        http_error = requests.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error

        with patch("toggl.requests.get", return_value=mock_response):
            result = api.get_cached_entries()

        assert result is None
        assert api._rate_limited is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
