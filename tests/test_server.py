"""Unit tests for server.py's /sync secret check and OAuth bootstrap routes."""

from unittest.mock import Mock, patch

import pytest

import server


def _make_handler(path, headers=None):
    """Build a SyncHandler without going through BaseHTTPRequestHandler.__init__
    (which expects a real socket) — the standard trick for unit testing it."""
    handler = server.SyncHandler.__new__(server.SyncHandler)
    handler.path = path
    handler.headers = headers or {}
    handler.wfile = Mock()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    return handler


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(server, "SYNC_SHARED_SECRET", "s3cret")
    monkeypatch.setattr(server, "PKCE_STATE_BUCKET", "test-bucket")


class TestSyncEndpoint:
    def test_rejects_missing_secret(self):
        handler = _make_handler("/sync", headers={})
        with patch("sync.main") as mock_main:
            handler.do_POST()
        mock_main.assert_not_called()
        handler.send_response.assert_called_once_with(403)

    def test_rejects_wrong_secret(self):
        handler = _make_handler("/sync", headers={"X-Sync-Secret": "wrong"})
        with patch("sync.main") as mock_main:
            handler.do_POST()
        mock_main.assert_not_called()
        handler.send_response.assert_called_once_with(403)

    def test_runs_sync_with_correct_secret(self):
        handler = _make_handler("/sync", headers={"X-Sync-Secret": "s3cret"})
        with patch("sync.main") as mock_main:
            handler.do_POST()
        mock_main.assert_called_once()
        handler.send_response.assert_called_once_with(200)

    def test_unknown_path_404(self):
        handler = _make_handler("/other", headers={"X-Sync-Secret": "s3cret"})
        handler.do_POST()
        handler.send_response.assert_called_once_with(404)

    def test_rejects_empty_secret_header_when_unconfigured(self, monkeypatch):
        """If SYNC_SHARED_SECRET is ever left unset (empty string default), an
        empty X-Sync-Secret header must not satisfy a naive equality check —
        that would silently disable the one real gate on this public service."""
        monkeypatch.setattr(server, "SYNC_SHARED_SECRET", "")
        handler = _make_handler("/sync", headers={"X-Sync-Secret": ""})
        with patch("sync.main") as mock_main:
            handler.do_POST()
        mock_main.assert_not_called()
        handler.send_response.assert_called_once_with(403)


class TestOAuthAuthorize:
    def test_rejects_missing_secret(self):
        handler = _make_handler("/oauth/authorize", headers={"Host": "svc.example.com"})
        handler.do_GET()
        handler.send_response.assert_called_once_with(403)

    def test_redirects_to_trakt_with_state_and_saves_verifier(self, monkeypatch):
        monkeypatch.setenv("TRAKT_CLIENT_ID", "client123")
        handler = _make_handler("/oauth/authorize?secret=s3cret", headers={"Host": "svc.example.com"})

        with patch("utils.save_json_file") as mock_save:
            handler.do_GET()

        handler.send_response.assert_called_once_with(302)
        location = handler.send_header.call_args.args[1]
        assert location.startswith("https://trakt.tv/oauth/authorize?")
        assert "redirect_uri=https%3A%2F%2Fsvc.example.com%2Foauth%2Fcallback" in location
        assert "state=s3cret%3A" in location

        mock_save.assert_called_once()
        saved_path, saved_data = mock_save.call_args.args
        assert saved_path.startswith("gs://test-bucket/pkce/")
        assert "code_verifier" in saved_data


class TestOAuthCallback:
    def test_rejects_missing_code(self):
        handler = _make_handler("/oauth/callback?state=s3cret:abc", headers={"Host": "svc.example.com"})
        handler.do_GET()
        handler.send_response.assert_called_once_with(403)

    def test_rejects_wrong_secret_in_state(self):
        handler = _make_handler("/oauth/callback?code=abc&state=wrong:xyz", headers={"Host": "svc.example.com"})
        handler.do_GET()
        handler.send_response.assert_called_once_with(403)

    def test_rejects_unknown_verifier(self):
        handler = _make_handler("/oauth/callback?code=abc&state=s3cret:xyz", headers={"Host": "svc.example.com"})
        with patch("utils.load_json_file", return_value=None):
            handler.do_GET()
        handler.send_response.assert_called_once_with(400)

    def test_exchanges_code_and_cleans_up_on_success(self, monkeypatch):
        monkeypatch.setenv("TRAKT_CLIENT_ID", "client123")
        monkeypatch.setenv("TRAKT_TOKEN_FILE", "gs://test-bucket/trakt_tokens.json")
        handler = _make_handler("/oauth/callback?code=abc123&state=s3cret:xyz", headers={"Host": "svc.example.com"})

        with patch("utils.load_json_file", return_value={"code_verifier": "verifier123"}):
            with patch("utils.delete_json_file") as mock_delete:
                with patch("trakt.TraktAPI._exchange_code_for_tokens") as mock_exchange:
                    handler.do_GET()

        mock_exchange.assert_called_once()
        assert mock_exchange.call_args.args[0] == "abc123"
        assert mock_exchange.call_args.args[1] == "verifier123"
        mock_delete.assert_called_once_with("gs://test-bucket/pkce/xyz.json")
        handler.send_response.assert_called_once_with(200)

    def test_exchange_failure_returns_500(self, monkeypatch):
        monkeypatch.setenv("TRAKT_CLIENT_ID", "client123")
        monkeypatch.setenv("TRAKT_TOKEN_FILE", "gs://test-bucket/trakt_tokens.json")
        handler = _make_handler("/oauth/callback?code=abc123&state=s3cret:xyz", headers={"Host": "svc.example.com"})

        with patch("utils.load_json_file", return_value={"code_verifier": "verifier123"}):
            with patch("trakt.TraktAPI._exchange_code_for_tokens", side_effect=RuntimeError("boom")):
                handler.do_GET()

        handler.send_response.assert_called_once_with(500)
