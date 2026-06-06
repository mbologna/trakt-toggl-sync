import json
import os
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

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

    def test_load_json_file_invalid_json(self, tmp_path):
        """Test loading invalid JSON file returns None."""
        test_file = tmp_path / "bad.json"
        test_file.write_text("{not valid json")

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

    def test_save_json_file_creates_parent_dirs(self, tmp_path):
        """Test saving JSON file creates parent directories if needed."""
        test_file = tmp_path / "nested" / "dir" / "test.json"
        utils.save_json_file(str(test_file), {"x": 1})
        assert test_file.exists()


class TestCheckRequiredEnvVariables:
    """Test environment variable validation."""

    def test_exits_when_variable_missing(self):
        required = [
            "TRAKT_CLIENT_ID",
            "TRAKT_CLIENT_SECRET",
            "TOGGL_API_TOKEN",
            "TOGGL_WORKSPACE_ID",
            "TOGGL_PROJECT_ID",
        ]
        clean_env = {k: v for k, v in os.environ.items() if k not in required}
        with patch.dict(os.environ, clean_env, clear=True):
            with pytest.raises(SystemExit):
                utils.check_required_env_variables()

    def test_passes_when_all_present(self):
        env = {
            "TRAKT_CLIENT_ID": "id",
            "TRAKT_CLIENT_SECRET": "secret",
            "TOGGL_API_TOKEN": "token",
            "TOGGL_WORKSPACE_ID": "123",
            "TOGGL_PROJECT_ID": "456",
        }
        with patch.dict(os.environ, env):
            utils.check_required_env_variables()  # should not raise


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

    def test_get_headers_without_token(self):
        api = TraktAPI("my_client_id", "secret", "tokens.json")
        headers = api._get_headers()
        assert headers["trakt-api-key"] == "my_client_id"
        assert headers["trakt-api-version"] == "2"
        assert "Authorization" not in headers

    def test_get_headers_with_token(self):
        api = TraktAPI("my_client_id", "secret", "tokens.json")
        headers = api._get_headers(access_token="mytoken")
        assert headers["Authorization"] == "Bearer mytoken"

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

    def test_format_entry_description_missing_fields(self):
        """format_entry_description uses 'Unknown' fallback for missing fields."""
        entry = {"type": "movie", "movie": {}}
        result = TraktAPI.format_entry_description(entry)
        assert "Unknown" in result


class TestTraktAuthenticate:
    """Test TraktAPI.authenticate() — device code OAuth flow."""

    def _make_api(self, tmp_path):
        return TraktAPI("client_id", "client_secret", str(tmp_path / "tokens.json"))

    def _device_data(self, expires_in=600):
        return {
            "user_code": "ABCD1234",
            "device_code": "device_code_123",
            "verification_url": "https://trakt.tv/activate",
            "interval": 0,
            "expires_in": expires_in,
        }

    def test_authenticate_success(self, tmp_path):
        """Successful auth on first poll returns tokens and saves the token file."""
        api = self._make_api(tmp_path)
        device_mock = Mock()
        device_mock.json.return_value = self._device_data()

        token_mock = Mock()
        token_mock.status_code = 200
        token_mock.json.return_value = {"access_token": "acc", "refresh_token": "ref", "expires_in": 7776000}

        with patch("requests.post", side_effect=[device_mock, token_mock]):
            tokens = api.authenticate()

        assert tokens["access_token"] == "acc"
        assert "expires_at" in tokens
        saved = json.loads((tmp_path / "tokens.json").read_text())
        assert saved["access_token"] == "acc"

    def test_authenticate_polls_through_pending_then_succeeds(self, tmp_path):
        """400 (pending) responses are retried until success."""
        api = self._make_api(tmp_path)
        device_mock = Mock()
        device_mock.json.return_value = self._device_data()

        pending = Mock()
        pending.status_code = 400
        success = Mock()
        success.status_code = 200
        success.json.return_value = {"access_token": "acc", "refresh_token": "ref", "expires_in": 7776000}

        with patch("requests.post", side_effect=[device_mock, pending, pending, success]):
            tokens = api.authenticate()

        assert tokens["access_token"] == "acc"

    def test_authenticate_timeout_when_expires_immediately(self, tmp_path):
        """When expires_in=0 the loop never runs and raises a timed-out RuntimeError."""
        api = self._make_api(tmp_path)
        device_mock = Mock()
        device_mock.json.return_value = self._device_data(expires_in=0)

        with patch("requests.post", return_value=device_mock):
            with pytest.raises(RuntimeError, match="timed out"):
                api.authenticate()

    def test_authenticate_410_breaks_immediately(self, tmp_path):
        """410 (device code expired on server) stops polling and raises RuntimeError."""
        api = self._make_api(tmp_path)
        device_mock = Mock()
        device_mock.json.return_value = self._device_data()

        expired_mock = Mock()
        expired_mock.status_code = 410

        with patch("requests.post", side_effect=[device_mock, expired_mock]):
            with pytest.raises(RuntimeError, match="Authentication failed"):
                api.authenticate()

    def test_authenticate_unexpected_status_raises(self, tmp_path):
        """An unrecognised HTTP status breaks the loop and raises RuntimeError."""
        api = self._make_api(tmp_path)
        device_mock = Mock()
        device_mock.json.return_value = self._device_data()

        error_mock = Mock()
        error_mock.status_code = 500

        with patch("requests.post", side_effect=[device_mock, error_mock]):
            with pytest.raises(RuntimeError, match="Authentication failed"):
                api.authenticate()

    def test_authenticate_418_is_treated_as_pending(self, tmp_path):
        """418 (denied / slow-down variants) keeps polling; eventually succeeds."""
        api = self._make_api(tmp_path)
        device_mock = Mock()
        device_mock.json.return_value = self._device_data()

        denied = Mock()
        denied.status_code = 418
        success = Mock()
        success.status_code = 200
        success.json.return_value = {"access_token": "acc", "refresh_token": "ref", "expires_in": 7776000}

        with patch("requests.post", side_effect=[device_mock, denied, success]):
            tokens = api.authenticate()

        assert tokens["access_token"] == "acc"


class TestTraktRefreshToken:
    """Test TraktAPI.refresh_token()."""

    def _make_api(self, tmp_path):
        return TraktAPI("client_id", "client_secret", str(tmp_path / "tokens.json"))

    def test_refresh_token_success(self, tmp_path):
        api = self._make_api(tmp_path)
        response_mock = Mock()
        response_mock.json.return_value = {"access_token": "new_acc", "refresh_token": "new_ref", "expires_in": 7776000}

        with patch("requests.post", return_value=response_mock):
            tokens = api.refresh_token("old_refresh")

        assert tokens["access_token"] == "new_acc"
        assert "expires_at" in tokens

    def test_refresh_token_400_deletes_file_and_reauthenticates(self, tmp_path):
        """On 400 the stale token file is removed and authenticate() is called."""
        token_file = tmp_path / "tokens.json"
        token_file.write_text('{"old": true}')
        api = self._make_api(tmp_path)

        error_response = Mock()
        error_response.status_code = 400
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = http_error
            with patch.object(api, "authenticate", return_value={"access_token": "fresh"}) as mock_auth:
                result = api.refresh_token("expired_refresh")

        assert not token_file.exists()
        mock_auth.assert_called_once()
        assert result == {"access_token": "fresh"}

    def test_refresh_token_other_http_error_reraises(self, tmp_path):
        """Non-400 HTTP errors are re-raised unchanged."""
        api = self._make_api(tmp_path)
        error_response = Mock()
        error_response.status_code = 500
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = http_error
            with pytest.raises(requests.exceptions.HTTPError):
                api.refresh_token("some_refresh")


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


class TestTogglGetCachedEntries:
    """Test TogglAPI.get_cached_entries() caching and rate-limit behaviour."""

    def _make_api(self):
        return TogglAPI("token", 123, 456, ["trakt"])

    def test_fetches_on_first_call(self):
        api = self._make_api()
        entries = [{"id": 1}]
        response_mock = Mock()
        response_mock.json.return_value = entries

        with patch("requests.get", return_value=response_mock):
            result = api.get_cached_entries()

        assert result == entries

    def test_uses_cache_on_second_call(self):
        api = self._make_api()
        response_mock = Mock()
        response_mock.json.return_value = []

        with patch("requests.get", return_value=response_mock) as mock_get:
            api.get_cached_entries()
            api.get_cached_entries()

        assert mock_get.call_count == 1

    def test_force_refresh_bypasses_cache(self):
        api = self._make_api()
        response_mock = Mock()
        response_mock.json.return_value = []

        with patch("requests.get", return_value=response_mock) as mock_get:
            api.get_cached_entries()
            api.get_cached_entries(force_refresh=True)

        assert mock_get.call_count == 2

    def test_rate_limit_returns_none_and_sets_flag(self):
        """402 response sets _rate_limited and returns None."""
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 402
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch("requests.get") as mock_get:
            mock_get.return_value.raise_for_status.side_effect = http_error
            result = api.get_cached_entries()

        assert result is None
        assert api._rate_limited is True


class TestTogglFindExistingEntry:
    """Test TogglAPI.find_existing_entry() matching logic."""

    def _make_api(self):
        return TogglAPI("token", 123, 456, ["trakt"])

    def _sample_entry(self, **overrides):
        base = {
            "description": "🎞️ The Matrix (1999)",
            "start": "2025-01-01T10:00:00Z",
            "stop": "2025-01-01T12:00:00Z",
            "project_id": 456,
            "tags": ["trakt"],
            "wid": 123,
        }
        base.update(overrides)
        return base

    def test_finds_matching_entry(self):
        api = self._make_api()
        entry = self._sample_entry()
        api._cached_entries = [entry]
        api._cache_timestamp = time.time()

        result = api.find_existing_entry("🎞️ The Matrix (1999)", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")
        assert result == entry

    def test_no_match_on_different_description(self):
        api = self._make_api()
        api._cached_entries = [self._sample_entry()]
        api._cache_timestamp = time.time()

        result = api.find_existing_entry("Different Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")
        assert result is None

    def test_no_match_on_different_times(self):
        api = self._make_api()
        api._cached_entries = [self._sample_entry()]
        api._cache_timestamp = time.time()

        result = api.find_existing_entry("🎞️ The Matrix (1999)", "2025-01-01T09:00:00Z", "2025-01-01T11:00:00Z")
        assert result is None

    def test_skips_entry_without_stop(self):
        """Entries with no stop time (running timers) are ignored."""
        api = self._make_api()
        entry = self._sample_entry()
        del entry["stop"]
        api._cached_entries = [entry]
        api._cache_timestamp = time.time()

        result = api.find_existing_entry("🎞️ The Matrix (1999)", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")
        assert result is None

    def test_no_match_on_different_project(self):
        api = self._make_api()
        api._cached_entries = [self._sample_entry(project_id=999)]
        api._cache_timestamp = time.time()

        result = api.find_existing_entry("🎞️ The Matrix (1999)", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")
        assert result is None


class TestTogglCreateEntry:
    """Test TogglAPI.create_entry()."""

    def _make_api(self):
        api = TogglAPI("token", 123, 456, ["trakt"])
        # Prime the cache so get_cached_entries() doesn't hit the network
        api._cached_entries = []
        api._cache_timestamp = time.time()
        return api

    def test_create_entry_success(self):
        api = self._make_api()
        response_mock = Mock()
        response_mock.json.return_value = {"id": 999}

        with patch("requests.post", return_value=response_mock):
            entry_id = api.create_entry("Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        assert entry_id == 999
        assert api._cached_entries is None  # cache invalidated after creation

    def test_create_entry_skips_when_rate_limited(self):
        api = self._make_api()
        with patch.object(api, "get_cached_entries", return_value=None):
            with patch("requests.post") as mock_post:
                result = api.create_entry("Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        mock_post.assert_not_called()
        assert result is None

    def test_create_entry_skips_existing(self):
        """If the entry already exists, skip the POST and return its id."""
        api = self._make_api()
        existing = {
            "id": 42,
            "description": "Test Movie",
            "start": "2025-01-01T10:00:00Z",
            "stop": "2025-01-01T12:00:00Z",
            "project_id": 456,
            "tags": ["trakt"],
            "wid": 123,
        }
        api._cached_entries = [existing]

        with patch("requests.post") as mock_post:
            result = api.create_entry("Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        mock_post.assert_not_called()
        assert result == 42

    def test_create_entry_402_sets_rate_limited_and_raises(self):
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 402
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = http_error
            with pytest.raises(requests.exceptions.HTTPError):
                api.create_entry("Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        assert api._rate_limited is True


class TestTogglUpdateEntry:
    """Test TogglAPI.update_entry()."""

    def _make_api(self):
        return TogglAPI("token", 123, 456, ["trakt"])

    def test_update_entry_success(self):
        api = self._make_api()
        response_mock = Mock()
        response_mock.json.return_value = {"id": 42}

        with patch("requests.put", return_value=response_mock):
            result = api.update_entry(42, "Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        assert result == 42
        assert api._cached_entries is None  # cache invalidated

    def test_update_entry_404_returns_none(self):
        """404 means the entry was deleted in Toggl; return None gracefully."""
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 404
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch("requests.put") as mock_put:
            mock_put.return_value.raise_for_status.side_effect = http_error
            result = api.update_entry(42, "Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        assert result is None

    def test_update_entry_402_sets_rate_limited_and_raises(self):
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 402
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch("requests.put") as mock_put:
            mock_put.return_value.raise_for_status.side_effect = http_error
            with pytest.raises(requests.exceptions.HTTPError):
                api.update_entry(42, "Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        assert api._rate_limited is True


class TestSyncProcessHistoryItem:
    """Test sync.process_history_item() for movies and episodes."""

    def _make_toggl(self):
        api = TogglAPI("token", 123, 456, ["trakt"])
        api._cached_entries = []
        api._cache_timestamp = time.time()
        return api

    def _movie_item(self):
        return {
            "type": "movie",
            "watched_at": "2025-01-01T12:00:00.000Z",
            "movie": {
                "title": "The Matrix",
                "year": 1999,
                "runtime": 136,
                "ids": {"trakt": 1},
            },
        }

    def _episode_item(self):
        return {
            "type": "episode",
            "watched_at": "2025-01-01T12:00:00.000Z",
            "show": {"title": "Breaking Bad"},
            "episode": {
                "season": 1,
                "number": 1,
                "title": "Pilot",
                "runtime": 58,
                "ids": {"trakt": 10},
            },
        }

    def test_creates_movie_entry_and_saves_state(self, tmp_path):
        from sync import process_history_item

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        sync_state = {}

        with patch.object(toggl, "create_entry", return_value=999) as mock_create:
            process_history_item(self._movie_item(), toggl, sync_state, state_file)

        mock_create.assert_called_once()
        assert "The Matrix" in mock_create.call_args.kwargs["description"]
        assert sync_state["movie:1"] == 999

    def test_creates_episode_entry_and_saves_state(self, tmp_path):
        from sync import process_history_item

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        sync_state = {}

        with patch.object(toggl, "create_entry", return_value=888):
            process_history_item(self._episode_item(), toggl, sync_state, state_file)

        assert sync_state["episode:10"] == 888

    def test_updates_existing_state_entry(self, tmp_path):
        """If state already has an id for this item, update_entry is called instead."""
        from sync import process_history_item

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        sync_state = {"movie:1": 42}

        with patch.object(toggl, "update_entry", return_value=42) as mock_update:
            with patch.object(toggl, "create_entry") as mock_create:
                process_history_item(self._movie_item(), toggl, sync_state, state_file)

        mock_update.assert_called_once()
        mock_create.assert_not_called()
        assert sync_state["movie:1"] == 42

    def test_recreates_when_update_returns_none(self, tmp_path):
        """If update returns None (entry deleted in Toggl), a new entry is created."""
        from sync import process_history_item

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        sync_state = {"movie:1": 42}

        with patch.object(toggl, "update_entry", return_value=None):
            with patch.object(toggl, "create_entry", return_value=999) as mock_create:
                process_history_item(self._movie_item(), toggl, sync_state, state_file)

        mock_create.assert_called_once()
        assert sync_state["movie:1"] == 999

    def test_state_file_persisted_to_disk(self, tmp_path):
        """State is written to disk after each item so it survives crashes."""
        from sync import process_history_item

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        sync_state = {}

        with patch.object(toggl, "create_entry", return_value=777):
            process_history_item(self._movie_item(), toggl, sync_state, state_file)

        on_disk = json.loads((tmp_path / "state.json").read_text())
        assert on_disk["movie:1"] == 777


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
