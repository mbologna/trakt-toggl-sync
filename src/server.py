#!/usr/bin/env python3
"""Minimal HTTP trigger + browser-based OAuth bootstrap for trakt-toggl-sync.

- Cloud Scheduler sends a POST /sync to invoke a sync cycle.
- GET /oauth/authorize and GET /oauth/callback complete the Trakt PKCE flow
  from any browser, with no local machine involved — Cloud Run's filesystem
  is ephemeral and can't run the local loopback callback server authenticate()
  uses for `make run`, so this reuses the same underlying PKCE helpers
  (TraktAPI._generate_pkce_pair / _exchange_code_for_tokens) with the Cloud
  Run service's own URL as the redirect_uri instead.

The service is public (Cloud Run invoker allUsers) — SYNC_SHARED_SECRET is
the real gate on both /sync and /oauth/authorize, not Cloud Run's IAM layer.
"""

import http.server
import os
import sys
import uuid

PORT = int(os.environ.get("PORT", 8080))
SYNC_SHARED_SECRET = os.environ.get("SYNC_SHARED_SECRET", "")
PKCE_STATE_BUCKET = os.environ.get("PKCE_STATE_BUCKET", "")


class SyncHandler(http.server.BaseHTTPRequestHandler):
    def _respond(self, status, body, content_type="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def _query(self):
        from urllib.parse import parse_qs, urlparse

        return parse_qs(urlparse(self.path).query)

    def do_POST(self):
        if self.path == "/sync":
            if self.headers.get("X-Sync-Secret") != SYNC_SHARED_SECRET:
                self._respond(403, "forbidden")
                return
            try:
                from sync import main

                main()
                self._respond(200, "ok")
            except Exception as e:
                print(f"[server] Sync failed: {e}", file=sys.stderr, flush=True)
                self._respond(500, str(e))
        else:
            self._respond(404, "not found")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/oauth/authorize":
            self._oauth_authorize()
        elif path == "/oauth/callback":
            self._oauth_callback()
        else:
            self._respond(404, "not found")

    def _oauth_authorize(self):
        from urllib.parse import urlencode

        from trakt import TraktAPI

        query = self._query()
        if query.get("secret", [None])[0] != SYNC_SHARED_SECRET:
            self._respond(403, "forbidden")
            return

        redirect_uri = f"https://{self.headers.get('Host')}/oauth/callback"
        code_verifier, code_challenge = TraktAPI._generate_pkce_pair()
        verifier_id = uuid.uuid4().hex

        from utils import save_json_file

        save_json_file(f"gs://{PKCE_STATE_BUCKET}/pkce/{verifier_id}.json", {"code_verifier": code_verifier})

        params = {
            "client_id": os.environ["TRAKT_CLIENT_ID"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # Round-trips the shared secret (so a stranger can't complete this
            # flow even if they guess the URL) and the verifier's storage key.
            "state": f"{SYNC_SHARED_SECRET}:{verifier_id}",
        }
        authorize_url = f"{TraktAPI.AUTH_URL}/oauth/authorize?{urlencode(params)}"
        self.send_response(302)
        self.send_header("Location", authorize_url)
        self.end_headers()

    def _oauth_callback(self):
        from trakt import TraktAPI
        from utils import load_json_file

        query = self._query()
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0] or ""
        secret, _, verifier_id = state.partition(":")

        if not code or secret != SYNC_SHARED_SECRET or not verifier_id:
            self._respond(403, "forbidden")
            return

        verifier_path = f"gs://{PKCE_STATE_BUCKET}/pkce/{verifier_id}.json"
        stored = load_json_file(verifier_path)
        if stored is None:
            self._respond(400, "unknown or expired authorization attempt")
            return

        try:
            trakt = TraktAPI(
                os.environ["TRAKT_CLIENT_ID"],
                os.environ["TRAKT_TOKEN_FILE"],
                redirect_uri=f"https://{self.headers.get('Host')}/oauth/callback",
            )
            trakt._exchange_code_for_tokens(code, stored["code_verifier"])
        except Exception as e:
            print(f"[server] OAuth exchange failed: {e}", file=sys.stderr, flush=True)
            self._respond(500, f"authorization failed: {e}")
            return

        from utils import delete_json_file

        delete_json_file(verifier_path)
        self._respond(200, "<html><body>Authorized. You can close this tab.</body></html>", "text/html")

    def log_message(self, format, *args):
        pass  # sync.py handles its own structured logging


if __name__ == "__main__":
    server = http.server.HTTPServer(("", PORT), SyncHandler)
    print(f"[server] Listening on port {PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
