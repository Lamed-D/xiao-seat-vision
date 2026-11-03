"""Mock of the Supabase Edge Functions for the Docker integration test.

Records every request; emulates idempotency by event_id / snapshot_id.
GET /_requests returns the log. No Supabase project or secrets involved.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("MOCK_TOKEN", "")
REQUESTS: list[dict] = []
SEEN: set[str] = set()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/_requests":
            return self._send(200, REQUESTS)
        self._send(404, {})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        fn = self.path.rsplit("/", 1)[-1]
        authorized = self.headers.get("x-bridge-token") == TOKEN
        REQUESTS.append({"fn": fn, "authorized": authorized, "body": body})
        if not authorized:
            return self._send(401, {"error": "unauthorized"})
        key = body.get("event_id") or body.get("snapshot_id")
        if key and key in SEEN:
            return self._send(200, {"status": "duplicate"})
        if key:
            SEEN.add(key)
        self._send(201 if key else 200, {"status": "created" if key else "stored"})

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
