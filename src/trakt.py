"""Trakt API client for managing viewing history."""

import base64
import datetime as dt
import hashlib
import http.server
import ipaddress
import os
import secrets
import ssl
import sys
import tempfile
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from utils import save_json_file, timestamp


class _CallbackServer(http.server.HTTPServer):
    """One-shot-per-request server that silently drops stray/broken connections.

    Browsers often open preliminary connections (cert probing, prefetch) before
    the real PKCE callback request, which would otherwise print a noisy traceback.
    """

    def handle_error(self, request, client_address):
        pass


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the ?code=... (or ?error=...) query param from the PKCE redirect."""

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        self.server.auth_code = query.get("code", [None])[0]
        self.server.auth_error = query.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Authorized. You can close this tab.</body></html>")

    def log_message(self, format, *args):  # noqa: A002
        pass


class TraktAPI:
    """Trakt API client for managing viewing history."""

    BASE_URL = "https://api.trakt.tv"
    AUTH_URL = "https://trakt.tv"
    REDIRECT_PORT = 8843
    # Must exactly match the redirect_uri registered on the Trakt app.
    REDIRECT_URI = f"https://127.0.0.1:{REDIRECT_PORT}/callback"
    DEFAULT_TIMEOUT = (3.05, 10)

    def __init__(self, client_id, token_file):
        self.client_id = client_id
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

    @staticmethod
    def _generate_pkce_pair():
        """Generate a PKCE code_verifier and its S256 code_challenge."""
        code_verifier = secrets.token_urlsafe(64)[:128]
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return code_verifier, code_challenge

    def _exchange_code_for_tokens(self, code, code_verifier):
        """Exchange an authorization code for an access/refresh token pair."""
        response = requests.post(
            f"{self.BASE_URL}/oauth/token",
            json={
                "code": code,
                "client_id": self.client_id,
                "redirect_uri": self.REDIRECT_URI,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
            timeout=self.DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        tokens = response.json()
        tokens["expires_at"] = (datetime.now() + timedelta(seconds=tokens["expires_in"])).isoformat()
        save_json_file(self.token_file, tokens)
        return tokens

    @staticmethod
    def _generate_self_signed_cert():
        """Generate an ephemeral self-signed cert/key pair for 127.0.0.1."""
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        return cert_pem, key_pem

    def _start_callback_server(self):
        """Start a one-shot local HTTPS server to receive the PKCE redirect."""
        cert_pem, key_pem = self._generate_self_signed_cert()
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as cert_file:
            cert_file.write(cert_pem)
            cert_path = cert_file.name
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as key_file:
            key_file.write(key_pem)
            key_path = key_file.name

        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_path, key_path)
        finally:
            os.remove(cert_path)
            os.remove(key_path)

        httpd = _CallbackServer(("127.0.0.1", self.REDIRECT_PORT), _CallbackHandler)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        httpd.auth_code = None
        httpd.auth_error = None
        return httpd

    def authenticate(self, auth_timeout=300):
        """Authenticate with Trakt via the PKCE authorization flow."""
        code_verifier, code_challenge = self._generate_pkce_pair()
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.REDIRECT_URI,
            "response_type": "code",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        authorize_url = f"{self.AUTH_URL}/oauth/authorize?{urlencode(params)}"
        httpd = self._start_callback_server()

        print(f"[{timestamp()}] Visit {authorize_url}")
        print(f"[{timestamp()}] (your browser will warn about the self-signed localhost certificate — proceed anyway)")
        sys.stdout.flush()
        webbrowser.open(authorize_url)

        # handle_request() serves exactly one connection; browsers often open stray
        # preliminary connections (cert probing, prefetch) before the real callback
        # arrives, so keep serving until we actually get a code or truly time out.
        httpd.timeout = 1.0
        deadline = datetime.now() + timedelta(seconds=auth_timeout)
        while httpd.auth_code is None and httpd.auth_error is None and datetime.now() < deadline:
            httpd.handle_request()
        httpd.server_close()

        if httpd.auth_error or not httpd.auth_code:
            raise RuntimeError(f"[{timestamp()}] Authentication failed: {httpd.auth_error or 'no code received'}")

        tokens = self._exchange_code_for_tokens(httpd.auth_code, code_verifier)
        print(f"[{timestamp()}] Authentication successful!")
        sys.stdout.flush()
        return tokens

    def refresh_token(self, refresh_token):
        """Refresh Trakt access token."""
        try:
            response = requests.post(
                f"{self.BASE_URL}/oauth/token",
                json={
                    "client_id": self.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                timeout=self.DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            tokens = response.json()
            tokens["expires_at"] = (datetime.now() + timedelta(seconds=tokens["expires_in"])).isoformat()
            save_json_file(self.token_file, tokens)
            print(f"[{timestamp()}] Token refreshed successfully!")
            sys.stdout.flush()
            return tokens
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                print(f"[{timestamp()}] Refresh token expired. Re-authenticating...")
                sys.stdout.flush()
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
        sys.stdout.flush()
        while True:
            response = requests.get(
                f"{self.BASE_URL}/sync/history",
                headers=headers,
                params={"page": page, "limit": 1000},
                timeout=self.DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            if not data:
                break
            history.extend(data)
            page += 1

        print(f"[{timestamp()}] Fetched {len(history)} total Trakt history entries")
        sys.stdout.flush()
        return history

    def fetch_history(self, access_token, start_date):
        """Fetch viewing history from Trakt starting from a specific date."""
        headers = self._get_headers(access_token)
        history = []
        page = 1

        while True:
            response = requests.get(
                f"{self.BASE_URL}/sync/history",
                headers=headers,
                params={"extended": "full", "start_at": start_date, "page": page, "limit": 100},
                timeout=self.DEFAULT_TIMEOUT,
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
        sys.stdout.flush()
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
            sys.stdout.flush()
            for dup in duplicates:
                desc = self.format_entry_description(dup)
                watched = dup["watched_at"][:10]
                print(f"  - {desc} (watched: {watched})")
                sys.stdout.flush()

            headers = self._get_headers(access_token)
            payload = {"ids": [entry["id"] for entry in duplicates]}
            response = requests.post(
                f"{self.BASE_URL}/sync/history/remove", headers=headers, json=payload, timeout=self.DEFAULT_TIMEOUT
            )
            if response.status_code == 200:
                print(f"[{timestamp()}] ✓ Successfully removed {len(duplicates)} duplicate Trakt entries")
                sys.stdout.flush()
            else:
                print(f"[{timestamp()}] ✗ Failed to delete Trakt duplicates: {response.status_code}")
                sys.stdout.flush()
        else:
            print(f"[{timestamp()}] No Trakt duplicates found")
            sys.stdout.flush()
