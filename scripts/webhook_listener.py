#!/usr/bin/env python3
"""Lightweight webhook receiver for Gitea push events."""

import hashlib
import hmac
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
DEPLOY_SCRIPT = "/mnt/user/appdata/seraphim/scripts/deploy.sh"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/deploy":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Verify HMAC signature
        signature = self.headers.get("X-Gitea-Signature", "")
        expected = hmac.new(
            WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            self.send_error(403)
            return

        # Trigger deploy in background
        subprocess.Popen(
            [DEPLOY_SCRIPT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"deploying"}')

    def log_message(self, format, *args):
        # Suppress default logging; use container logs instead
        pass


def run():
    server = HTTPServer(("0.0.0.0", 9000), Handler)
    print("Webhook listener on :9000")
    server.serve_forever()


if __name__ == "__main__":
    run()
