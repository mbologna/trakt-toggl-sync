import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

# Add parent directory to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
