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


class TestUtilityFunctionsGCS:
    """Test load/save/delete_json_file's gs:// branch (used on Cloud Run, where
    the filesystem is ephemeral) against a mocked google.cloud.storage.Client."""

    def _mock_client(self, monkeypatch):
        mock_client_cls = Mock()
        mock_blob = Mock()
        mock_client_cls.return_value.bucket.return_value.blob.return_value = mock_blob
        monkeypatch.setattr("google.cloud.storage.Client", mock_client_cls)
        return mock_client_cls, mock_blob

    def test_load_json_file_gcs_success(self, monkeypatch):
        _, mock_blob = self._mock_client(monkeypatch)
        mock_blob.download_as_text.return_value = '{"access_token": "abc"}'

        result = utils.load_json_file("gs://my-bucket/pkce/abc.json")

        assert result == {"access_token": "abc"}

    def test_load_json_file_gcs_uses_correct_bucket_and_blob(self, monkeypatch):
        mock_client_cls, mock_blob = self._mock_client(monkeypatch)
        mock_blob.download_as_text.return_value = "{}"

        utils.load_json_file("gs://my-bucket/nested/path/file.json")

        mock_client_cls.return_value.bucket.assert_called_once_with("my-bucket")
        mock_client_cls.return_value.bucket.return_value.blob.assert_called_once_with("nested/path/file.json")

    def test_load_json_file_gcs_not_found_returns_none(self, monkeypatch):
        from google.cloud.exceptions import NotFound

        _, mock_blob = self._mock_client(monkeypatch)
        mock_blob.download_as_text.side_effect = NotFound("no such object")

        result = utils.load_json_file("gs://my-bucket/missing.json")

        assert result is None

    def test_load_json_file_gcs_empty_returns_none(self, monkeypatch):
        _, mock_blob = self._mock_client(monkeypatch)
        mock_blob.download_as_text.return_value = "   "

        assert utils.load_json_file("gs://my-bucket/empty.json") is None

    def test_load_json_file_gcs_invalid_json_returns_none(self, monkeypatch):
        _, mock_blob = self._mock_client(monkeypatch)
        mock_blob.download_as_text.return_value = "{not valid json"

        assert utils.load_json_file("gs://my-bucket/bad.json") is None

    def test_save_json_file_gcs_uploads_with_content_type(self, monkeypatch):
        mock_client_cls, mock_blob = self._mock_client(monkeypatch)

        utils.save_json_file("gs://my-bucket/state.json", {"key": "value"})

        mock_client_cls.return_value.bucket.assert_called_once_with("my-bucket")
        mock_client_cls.return_value.bucket.return_value.blob.assert_called_once_with("state.json")
        mock_blob.upload_from_string.assert_called_once()
        uploaded_content, kwargs = (
            mock_blob.upload_from_string.call_args.args[0],
            mock_blob.upload_from_string.call_args.kwargs,
        )
        assert json.loads(uploaded_content) == {"key": "value"}
        assert kwargs["content_type"] == "application/json"

    def test_delete_json_file_gcs_deletes_blob(self, monkeypatch):
        _, mock_blob = self._mock_client(monkeypatch)

        utils.delete_json_file("gs://my-bucket/state.json")

        mock_blob.delete.assert_called_once()

    def test_delete_json_file_gcs_not_found_is_noop(self, monkeypatch):
        from google.cloud.exceptions import NotFound

        _, mock_blob = self._mock_client(monkeypatch)
        mock_blob.delete.side_effect = NotFound("already gone")

        utils.delete_json_file("gs://my-bucket/already-gone.json")  # must not raise


class TestCheckRequiredEnvVariables:
    """Test environment variable validation."""

    def test_exits_when_variable_missing(self):
        required = [
            "TRAKT_CLIENT_ID",
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
        api = TraktAPI("client_id", "token.json")
        expired_time = (datetime.now() - timedelta(hours=1)).isoformat()
        assert api.is_token_near_expiration(expired_time) is True

    def test_is_token_near_expiration_valid(self):
        """Test token expiration check for valid token."""
        api = TraktAPI("client_id", "token.json")
        future_time = (datetime.now() + timedelta(hours=2)).isoformat()
        assert api.is_token_near_expiration(future_time) is False

    def test_get_headers_without_token(self):
        api = TraktAPI("my_client_id", "tokens.json")
        headers = api._get_headers()
        assert headers["trakt-api-key"] == "my_client_id"
        assert headers["trakt-api-version"] == "2"
        assert "Authorization" not in headers

    def test_get_headers_with_token(self):
        api = TraktAPI("my_client_id", "tokens.json")
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


class TestTraktRemoveDuplicates:
    """Test TraktAPI.remove_duplicates()."""

    def _make_api(self):
        return TraktAPI("client_id", "tokens.json")

    def test_keeps_most_recently_watched_per_item(self):
        """Among entries sharing the same (type, trakt id), only the one with
        the latest watched_at survives; the rest are removed."""
        api = self._make_api()
        history = [
            {"id": 1, "type": "movie", "watched_at": "2025-01-01T10:00:00Z", "movie": {"ids": {"trakt": 100}}},
            {"id": 2, "type": "movie", "watched_at": "2025-01-05T10:00:00Z", "movie": {"ids": {"trakt": 100}}},
            {"id": 3, "type": "episode", "watched_at": "2025-01-01T10:00:00Z", "episode": {"ids": {"trakt": 200}}},
        ]

        with patch.object(api, "fetch_full_history", return_value=history):
            with patch("requests.post") as mock_post:
                mock_post.return_value.status_code = 200
                api.remove_duplicates("access_token")

        sent_ids = mock_post.call_args.kwargs["json"]["ids"]
        assert sent_ids == [1]

    def test_no_duplicates_skips_delete_call(self):
        api = self._make_api()
        history = [
            {"id": 1, "type": "movie", "watched_at": "2025-01-01T10:00:00Z", "movie": {"ids": {"trakt": 100}}},
            {"id": 2, "type": "episode", "watched_at": "2025-01-01T10:00:00Z", "episode": {"ids": {"trakt": 200}}},
        ]

        with patch.object(api, "fetch_full_history", return_value=history):
            with patch("requests.post") as mock_post:
                api.remove_duplicates("access_token")

        mock_post.assert_not_called()

    def test_entries_missing_trakt_id_are_ignored(self):
        """Entries with no resolvable trakt id can't be deduped and are left alone."""
        api = self._make_api()
        history = [
            {"id": 1, "type": "movie", "watched_at": "2025-01-01T10:00:00Z", "movie": {"ids": {}}},
            {"id": 2, "type": "movie", "watched_at": "2025-01-02T10:00:00Z", "movie": {"ids": {}}},
        ]

        with patch.object(api, "fetch_full_history", return_value=history):
            with patch("requests.post") as mock_post:
                api.remove_duplicates("access_token")

        mock_post.assert_not_called()


class TestTraktAuthenticate:
    """Test TraktAPI.authenticate() — PKCE OAuth flow."""

    def _make_api(self, tmp_path):
        return TraktAPI("client_id", str(tmp_path / "tokens.json"))

    def test_generate_pkce_pair_is_well_formed(self, tmp_path):
        """code_verifier length is within spec and code_challenge is its S256 hash."""
        import base64
        import hashlib

        verifier, challenge = TraktAPI._generate_pkce_pair()
        assert 43 <= len(verifier) <= 128
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
        assert challenge == expected

    def test_authenticate_success(self, tmp_path):
        """Receives the code via the local callback server, exchanges it, and saves the token file."""
        api = self._make_api(tmp_path)
        token_mock = Mock()
        token_mock.json.return_value = {"access_token": "acc", "refresh_token": "ref", "expires_in": 7776000}

        fake_httpd = Mock()
        fake_httpd.auth_code = None
        fake_httpd.auth_error = None

        def _simulate_callback():
            fake_httpd.auth_code = "auth_code_123"

        fake_httpd.handle_request.side_effect = _simulate_callback

        with patch("requests.post", return_value=token_mock) as mock_post:
            with patch.object(api, "_start_callback_server", return_value=fake_httpd):
                with patch("webbrowser.open"):
                    tokens = api.authenticate()

        assert tokens["access_token"] == "acc"
        assert "expires_at" in tokens
        saved = json.loads((tmp_path / "tokens.json").read_text())
        assert saved["access_token"] == "acc"
        fake_httpd.handle_request.assert_called_once()
        fake_httpd.server_close.assert_called_once()

        sent_json = mock_post.call_args.kwargs["json"]
        assert sent_json["code"] == "auth_code_123"
        assert sent_json["grant_type"] == "authorization_code"
        assert "code_verifier" in sent_json
        assert "client_secret" not in sent_json

    def test_authenticate_raises_when_no_code_received(self, tmp_path):
        """If the callback server never gets a code, authenticate() raises instead of hanging."""
        api = self._make_api(tmp_path)
        fake_httpd = Mock()
        fake_httpd.auth_code = None
        fake_httpd.auth_error = None

        with patch.object(api, "_start_callback_server", return_value=fake_httpd):
            with patch("webbrowser.open"):
                with pytest.raises(RuntimeError, match="Authentication failed"):
                    api.authenticate(auth_timeout=0.05)

    def test_authenticate_raises_on_http_error(self, tmp_path):
        """A failed code exchange propagates the HTTPError."""
        api = self._make_api(tmp_path)
        error_response = Mock()
        error_response.status_code = 400
        http_error = requests.exceptions.HTTPError(response=error_response)

        fake_httpd = Mock()
        fake_httpd.auth_code = "bad_code"
        fake_httpd.auth_error = None

        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = http_error
            with patch.object(api, "_start_callback_server", return_value=fake_httpd):
                with patch("webbrowser.open"):
                    with pytest.raises(requests.exceptions.HTTPError):
                        api.authenticate()


class TestTraktRefreshToken:
    """Test TraktAPI.refresh_token()."""

    def _make_api(self, tmp_path):
        return TraktAPI("client_id", str(tmp_path / "tokens.json"))

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

    def test_refresh_token_400_fails_fast_under_custom_redirect_uri(self, tmp_path):
        """A non-default redirect_uri means we're running under server.py's Cloud
        Run flow, which has no browser/loopback interface to complete the local
        authenticate() flow. On a 400 it must raise immediately with an actionable
        message instead of hanging on the local callback server until auth_timeout.
        """
        token_file = tmp_path / "tokens.json"
        token_file.write_text('{"old": true}')
        api = TraktAPI("client_id", str(token_file), redirect_uri="https://service.run.app/oauth/callback")

        error_response = Mock()
        error_response.status_code = 400
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = http_error
            with patch.object(api, "authenticate") as mock_auth:
                with pytest.raises(RuntimeError, match="/oauth/authorize"):
                    api.refresh_token("expired_refresh")

        mock_auth.assert_not_called()
        assert not token_file.exists()


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

    def test_start_date_uses_reports_api(self):
        """A start_date arg routes through the Reports API, not /me/time_entries."""
        api = self._make_api()
        with patch.object(api, "_fetch_reports_entries", return_value=[{"id": 1}]) as mock_fetch:
            with patch("requests.get") as mock_get:
                result = api.get_cached_entries(start_date="2025-01-01")

        assert result == [{"id": 1}]
        mock_fetch.assert_called_once()
        mock_get.assert_not_called()


class TestTogglReportsAPI:
    """Test TogglAPI._fetch_reports_page() / _fetch_reports_entries()."""

    def _make_api(self):
        return TogglAPI("token", 123, 456, ["trakt"])

    def _tags_response(self, tags=None):
        resp = Mock()
        resp.json.return_value = tags or [{"id": 1, "name": "trakt"}, {"id": 2, "name": "watching"}]
        return resp

    def _reports_response(self, rows, next_headers=None):
        resp = Mock()
        resp.json.return_value = rows
        resp.headers = next_headers or {}
        return resp

    def test_flattens_grouped_rows_and_resolves_tag_names(self):
        api = self._make_api()
        rows = [
            {
                "project_id": 456,
                "description": "🎞️ Movie X (2025)",
                "tag_ids": [1, 2],
                "time_entries": [{"id": 999, "start": "2025-01-01T10:00:00Z", "stop": "2025-01-01T12:00:00Z"}],
            }
        ]

        with patch("requests.get", return_value=self._tags_response()):
            with patch("requests.post", return_value=self._reports_response(rows)):
                entries = api._fetch_reports_entries("2025-01-01", "2025-01-02")

        assert entries == [
            {
                "id": 999,
                "project_id": 456,
                "start": "2025-01-01T10:00:00Z",
                "stop": "2025-01-01T12:00:00Z",
                "description": "🎞️ Movie X (2025)",
                "tags": ["trakt", "watching"],
                "wid": 123,
            }
        ]

    def test_paginates_using_next_headers(self):
        api = self._make_api()
        row1 = [
            {
                "project_id": 456,
                "description": "A",
                "tag_ids": [],
                "time_entries": [{"id": 1, "start": "2025-01-01T10:00:00Z", "stop": "2025-01-01T11:00:00Z"}],
            }
        ]
        row2 = [
            {
                "project_id": 456,
                "description": "B",
                "tag_ids": [],
                "time_entries": [{"id": 2, "start": "2025-01-02T10:00:00Z", "stop": "2025-01-02T11:00:00Z"}],
            }
        ]
        page1 = self._reports_response(
            row1, {"x-next-id": "2", "x-next-row-number": "2", "x-next-timestamp": "1700000000"}
        )
        page2 = self._reports_response(row2)

        with patch("requests.get", return_value=self._tags_response()):
            with patch("requests.post", side_effect=[page1, page2]) as mock_post:
                entries = api._fetch_reports_entries("2025-01-01", "2025-01-03")

        assert [e["id"] for e in entries] == [1, 2]
        assert mock_post.call_count == 2
        second_call_body = mock_post.call_args_list[1].kwargs["json"]
        assert second_call_body["first_id"] == 2
        assert second_call_body["first_row_number"] == 2
        assert second_call_body["first_timestamp"] == 1700000000

    def test_retries_on_402_then_succeeds(self):
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 402
        error_response.headers = {}
        http_error = requests.exceptions.HTTPError(response=error_response)

        rate_limited = Mock()
        rate_limited.raise_for_status.side_effect = http_error
        success = self._reports_response([])

        with patch("requests.get", return_value=self._tags_response()):
            with patch("requests.post", side_effect=[rate_limited, success]):
                with patch("time.sleep") as mock_sleep:
                    entries = api._fetch_reports_entries("2025-01-01", "2025-01-02")

        assert entries == []
        mock_sleep.assert_called_once()

    def test_tag_lookup_cached_across_calls(self):
        api = self._make_api()
        with patch("requests.get", return_value=self._tags_response()) as mock_get:
            with patch("requests.post", return_value=self._reports_response([])):
                api._fetch_reports_entries("2025-01-01", "2025-01-02")
                api._fetch_reports_entries("2025-02-01", "2025-02-02")

        assert mock_get.call_count == 1


class TestTogglHasOverlappingEntry:
    """Test TogglAPI.has_overlapping_entry()."""

    def _make_api(self):
        api = TogglAPI("token", 123, 456, ["trakt"])
        api._cached_entries = []
        api._cache_timestamp = time.time()
        return api

    def test_true_when_entry_starts_within_window(self):
        api = self._make_api()
        api._cached_entries = [
            {"project_id": 456, "start": "2025-06-01T19:00:00Z"},
        ]
        assert api.has_overlapping_entry("2025-06-01T21:00:00.000Z", runtime_minutes=120) is True

    def test_true_for_paused_session_within_buffer(self):
        """A Jellyfin session paused for hours still ends up inside the buffer window."""
        api = self._make_api()
        # window = [21:00 - 60min - 4h, 21:00] = [16:00, 21:00]; session started 16:30,
        # well before watched_at due to a pause, but still within the padded window.
        api._cached_entries = [
            {"project_id": 456, "start": "2025-06-01T16:30:00Z"},
        ]
        assert api.has_overlapping_entry("2025-06-01T21:00:00.000Z", runtime_minutes=60, buffer_hours=4) is True

    def test_false_when_entry_far_outside_window(self):
        api = self._make_api()
        api._cached_entries = [
            {"project_id": 456, "start": "2025-05-01T10:00:00Z"},
        ]
        assert api.has_overlapping_entry("2025-06-01T21:00:00.000Z", runtime_minutes=60) is False

    def test_false_when_entry_in_different_project(self):
        api = self._make_api()
        api._cached_entries = [
            {"project_id": 999, "start": "2025-06-01T20:00:00Z"},
        ]
        assert api.has_overlapping_entry("2025-06-01T21:00:00.000Z", runtime_minutes=60) is False

    def test_false_when_no_cached_entries(self):
        api = self._make_api()
        api._cached_entries = []
        assert api.has_overlapping_entry("2025-06-01T21:00:00.000Z", runtime_minutes=60) is False

    def test_false_when_rate_limited(self):
        """get_cached_entries() returning None (rate limited) must not look like a match."""
        api = self._make_api()
        api._cached_entries = None
        api._cache_timestamp = None
        with patch("requests.get") as mock_get:
            error_response = Mock()
            error_response.status_code = 402
            mock_get.return_value.raise_for_status.side_effect = requests.exceptions.HTTPError(response=error_response)
            result = api.has_overlapping_entry("2025-06-01T21:00:00.000Z", runtime_minutes=60)
        assert result is False


class TestTogglRemoveDuplicatesReportsAPI:
    """Test that remove_duplicates() fetches via the Reports API and filters by project."""

    def _make_api(self):
        return TogglAPI("token", 123, 456, ["trakt"])

    def test_filters_to_configured_project(self):
        api = self._make_api()
        entries = [
            {"id": 1, "project_id": 456, "description": "A", "start": "2025-06-01T10:00:00Z", "stop": None},
            {"id": 2, "project_id": 999, "description": "B", "start": "2025-06-01T10:00:00Z", "stop": None},
        ]

        with patch.object(api, "_fetch_reports_entries", return_value=entries) as mock_fetch:
            with patch("requests.delete") as mock_delete:
                api.remove_duplicates()

        mock_fetch.assert_called_once()
        mock_delete.assert_not_called()  # no duplicates among the one project-456 entry

    def test_402_during_fetch_skips_gracefully(self):
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 402
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch.object(api, "_fetch_reports_entries", side_effect=http_error):
            api.remove_duplicates()  # must not raise

    def test_non_402_error_during_fetch_reraises(self):
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 500
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch.object(api, "_fetch_reports_entries", side_effect=http_error):
            with pytest.raises(requests.exceptions.HTTPError):
                api.remove_duplicates()

    def test_exact_duplicates_keep_highest_id(self):
        """Among entries with identical (description, start, stop), all but the
        highest-id one are deleted."""
        api = self._make_api()
        entries = [
            {
                "id": 1,
                "project_id": 456,
                "description": "A",
                "start": "2025-06-01T10:00:00Z",
                "stop": "2025-06-01T11:00:00Z",
            },
            {
                "id": 2,
                "project_id": 456,
                "description": "A",
                "start": "2025-06-01T10:00:00Z",
                "stop": "2025-06-01T11:00:00Z",
            },
            {
                "id": 3,
                "project_id": 456,
                "description": "B",
                "start": "2025-06-02T10:00:00Z",
                "stop": "2025-06-02T11:00:00Z",
            },
        ]

        with patch.object(api, "_fetch_reports_entries", return_value=entries):
            with patch("requests.delete") as mock_delete:
                mock_delete.return_value.status_code = 200
                api.remove_duplicates()

        deleted_ids = {call.args[0].rsplit("/", 1)[-1] for call in mock_delete.call_args_list}
        assert deleted_ids == {"1"}

    def test_close_in_time_duplicates_keep_highest_id(self):
        """Same-description entries starting within 24h of each other are
        chained into one cluster; all but the highest-id one are deleted."""
        api = self._make_api()
        entries = [
            {
                "id": 10,
                "project_id": 456,
                "description": "Rewatch",
                "start": "2025-06-01T10:00:00Z",
                "stop": "2025-06-01T11:00:00Z",
            },
            {
                "id": 20,
                "project_id": 456,
                "description": "Rewatch",
                "start": "2025-06-01T20:00:00Z",
                "stop": "2025-06-01T21:00:00Z",
            },
            {
                "id": 5,
                "project_id": 456,
                "description": "Rewatch",
                "start": "2025-06-05T10:00:00Z",
                "stop": "2025-06-05T11:00:00Z",
            },
        ]

        with patch.object(api, "_fetch_reports_entries", return_value=entries):
            with patch("requests.delete") as mock_delete:
                mock_delete.return_value.status_code = 200
                api.remove_duplicates()

        # entries 10 and 20 are within 24h of each other (one cluster, keep 20);
        # entry 5 is >24h away from both, forming its own cluster of one (kept)
        deleted_ids = {call.args[0].rsplit("/", 1)[-1] for call in mock_delete.call_args_list}
        assert deleted_ids == {"10"}

    def test_close_in_time_pass_skips_entries_already_deleted_in_first_pass(self):
        """An entry removed as an exact duplicate in the first pass must not be
        deleted again in the close-in-time pass."""
        api = self._make_api()
        entries = [
            {
                "id": 1,
                "project_id": 456,
                "description": "A",
                "start": "2025-06-01T10:00:00Z",
                "stop": "2025-06-01T11:00:00Z",
            },
            {
                "id": 2,
                "project_id": 456,
                "description": "A",
                "start": "2025-06-01T10:00:00Z",
                "stop": "2025-06-01T11:00:00Z",
            },
        ]

        with patch.object(api, "_fetch_reports_entries", return_value=entries):
            with patch("requests.delete") as mock_delete:
                mock_delete.return_value.status_code = 200
                api.remove_duplicates()

        # Only one delete call total (id 1, from the exact-duplicate pass) — the
        # close-in-time pass must not issue a second delete call for the same pair.
        assert mock_delete.call_count == 1


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

    def test_create_entry_402_retries_then_raises_after_max_attempts(self):
        """Persistent 402s are retried with backoff before finally giving up."""
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 402
        error_response.headers = {}
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch("requests.post") as mock_post:
            mock_post.return_value.raise_for_status.side_effect = http_error
            with patch("time.sleep") as mock_sleep:
                with pytest.raises(requests.exceptions.HTTPError):
                    api.create_entry("Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        assert api._rate_limited is True
        assert mock_post.call_count == api.RATE_LIMIT_MAX_RETRIES + 1
        assert mock_sleep.call_count == api.RATE_LIMIT_MAX_RETRIES

    def test_create_entry_402_then_recovers(self):
        """A transient 402 followed by success returns the created entry's id."""
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 402
        error_response.headers = {}
        http_error = requests.exceptions.HTTPError(response=error_response)

        rate_limited_response = Mock()
        rate_limited_response.raise_for_status.side_effect = http_error
        success_response = Mock()
        success_response.json.return_value = {"id": 999}

        with patch("requests.post", side_effect=[rate_limited_response, success_response]):
            with patch("time.sleep") as mock_sleep:
                entry_id = api.create_entry("Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        assert entry_id == 999
        assert api._rate_limited is False
        mock_sleep.assert_called_once_with(api.RATE_LIMIT_RETRY_DELAY_SECONDS)

    def test_create_entry_402_honors_toggl_quota_reset_header(self):
        """When Toggl sends X-Toggl-Quota-Resets-In, wait that long instead of the fixed delay."""
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 402
        error_response.headers = {"x-toggl-quota-resets-in": "120"}
        http_error = requests.exceptions.HTTPError(response=error_response)

        rate_limited_response = Mock()
        rate_limited_response.raise_for_status.side_effect = http_error
        success_response = Mock()
        success_response.json.return_value = {"id": 999}

        with patch("requests.post", side_effect=[rate_limited_response, success_response]):
            with patch("time.sleep") as mock_sleep:
                entry_id = api.create_entry("Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        assert entry_id == 999
        mock_sleep.assert_called_once_with(120 + api.RATE_LIMIT_RETRY_BUFFER_SECONDS)


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

    def test_update_entry_402_retries_then_raises_after_max_attempts(self):
        """Persistent 402s are retried with backoff before finally giving up."""
        api = self._make_api()
        error_response = Mock()
        error_response.status_code = 402
        error_response.headers = {}
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch("requests.put") as mock_put:
            mock_put.return_value.raise_for_status.side_effect = http_error
            with patch("time.sleep") as mock_sleep:
                with pytest.raises(requests.exceptions.HTTPError):
                    api.update_entry(42, "Test Movie", "2025-01-01T10:00:00Z", "2025-01-01T12:00:00Z")

        assert api._rate_limited is True
        assert mock_put.call_count == api.RATE_LIMIT_MAX_RETRIES + 1
        assert mock_sleep.call_count == api.RATE_LIMIT_MAX_RETRIES


class TestSyncHistory:
    """Test sync.sync_history()'s graceful stop on rate limits and network errors."""

    def _make_toggl(self):
        api = TogglAPI("token", 123, 456, ["trakt"])
        api._cached_entries = []
        api._cache_timestamp = time.time()
        return api

    def test_processes_all_items_on_success(self, tmp_path):
        from sync import sync_history

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        history = [
            {"type": "movie", "watched_at": "2025-01-01T12:00:00.000Z", "movie": {"title": "A", "ids": {"trakt": 1}}},
            {"type": "movie", "watched_at": "2025-01-02T12:00:00.000Z", "movie": {"title": "B", "ids": {"trakt": 2}}},
        ]

        with patch.object(toggl, "create_entry", side_effect=[111, 222]) as mock_create:
            sync_history(history, toggl, {}, state_file)

        assert mock_create.call_count == 2

    def test_stops_gracefully_on_402(self, tmp_path):
        from sync import sync_history

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        history = [
            {"type": "movie", "watched_at": "2025-01-01T12:00:00.000Z", "movie": {"title": "A", "ids": {"trakt": 1}}},
        ]
        error_response = Mock()
        error_response.status_code = 402
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch.object(toggl, "create_entry", side_effect=http_error):
            sync_history(history, toggl, {}, state_file)  # must not raise

    def test_reraises_non_402_http_error(self, tmp_path):
        from sync import sync_history

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        history = [
            {"type": "movie", "watched_at": "2025-01-01T12:00:00.000Z", "movie": {"title": "A", "ids": {"trakt": 1}}},
        ]
        error_response = Mock()
        error_response.status_code = 500
        http_error = requests.exceptions.HTTPError(response=error_response)

        with patch.object(toggl, "create_entry", side_effect=http_error):
            with pytest.raises(requests.exceptions.HTTPError):
                sync_history(history, toggl, {}, state_file)

    def test_stops_gracefully_on_network_error(self, tmp_path):
        """A transient network error (timeout, connection reset) must not crash the run."""
        from sync import sync_history

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        history = [
            {"type": "movie", "watched_at": "2025-01-01T12:00:00.000Z", "movie": {"title": "A", "ids": {"trakt": 1}}},
        ]

        with patch.object(toggl, "create_entry", side_effect=requests.exceptions.ReadTimeout("timed out")):
            sync_history(history, toggl, {}, state_file)  # must not raise


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

    def test_skips_when_already_logged_via_jellyfin(self, tmp_path):
        """If has_overlapping_entry() finds a match, no Toggl write happens and no state is saved."""
        from sync import process_history_item

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        sync_state = {}

        with patch.object(toggl, "has_overlapping_entry", return_value=True):
            with patch.object(toggl, "create_entry") as mock_create:
                process_history_item(self._movie_item(), toggl, sync_state, state_file)

        mock_create.assert_not_called()
        assert sync_state == {}

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

    def test_handles_null_runtime(self, tmp_path):
        """Trakt sometimes returns runtime: null (not missing) for older titles."""
        from sync import process_history_item

        toggl = self._make_toggl()
        state_file = str(tmp_path / "state.json")
        sync_state = {}
        item = self._movie_item()
        item["movie"]["runtime"] = None

        with patch.object(toggl, "create_entry", return_value=1) as mock_create:
            process_history_item(item, toggl, sync_state, state_file)

        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["start_time"].startswith("2025-01-01T12:00:00")

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
