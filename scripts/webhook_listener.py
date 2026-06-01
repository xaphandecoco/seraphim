#!/usr/bin/env python3
"""Lightweight webhook receiver for Gitea push events."""

import hashlib
import hmac
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
DEPLOY_SCRIPT = "/mnt/user/appdata/seraphim/scripts/deploy.sh"
BIND_HOST = os.environ.get("WEBHOOK_BIND_HOST", "127.0.0.1")


def _validate_startup():
    if not WEBHOOK_SECRET or len(WEBHOOK_SECRET) < 32:
        print(
            "ERROR: WEBHOOK_SECRET is not set or is shorter than 32 characters. "
            "Set a strong secret before running the webhook listener.",
            file=sys.stderr,
        )
        sys.exit(1)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/deploy":
            self.send_error(404)
            return

        signature = self.headers.get("X-Gitea-Signature", "")
        if not signature:
            self.send_error(403)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        expected = hmac.new(
            WEBHOOK_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            self.send_error(403)
            return

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
        pass


def run():
    _validate_startup()
    server = HTTPServer((BIND_HOST, 9000), Handler)
    print(f"Webhook listener on {BIND_HOST}:9000")
    server.serve_forever()


if __name__ == "__main__":
    run()
