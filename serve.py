#!/usr/bin/env python3
"""ローカル確認用サーバー:  python3 serve.py   →  http://localhost:4321"""
import os, functools, http.server, socketserver

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 4321


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
    print(f"serving {ROOT} at http://localhost:{PORT}")
    httpd.serve_forever()
