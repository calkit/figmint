"""A localhost endpoint that opens a file in an editor.

This exists because of one stubborn constraint. The provenance panel wants an
"open this diagram" button, and the obvious implementation is a `vscode://file/…`
link — which works fine in Chrome, where the OS hands the URL to VS Code. But the
place this preview is most often read is VS Code's own Simple Browser, and a
webview will not follow a non-http scheme. It navigates to the URL and shows a
blank page. Labelling the link "external browser only" is honest but useless:
the button still does not work where it is needed.

A webview *will* follow `http://localhost`. So the button points here instead,
and this opens the editor on the machine running the preview.

Two details make it behave like a button rather than a link:

  * The response is `204 No Content`, which browsers treat as "stay where you
    are". Returning a page would navigate the reader away from the document
    they were reading.
  * It is only advertised when it is running. The plugin emits this link only
    when `FROMWHERE_OPEN_URL` is set, so a published HTML build gets a plain
    command instead of a link to a port on somebody else's laptop.

Scope: this binds to loopback, serves exactly one route, and refuses any path
outside the project it was started in. It is a development convenience, not a
service.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

#: Loopback only. This opens local applications on request; it has no business
#: being reachable from anywhere else.
HOST = "127.0.0.1"
DEFAULT_PORT = 8765

#: Environment variable the MyST plugin reads to decide whether to emit a link.
URL_ENV = "FROMWHERE_OPEN_URL"
#: Override for the command used to open a file.
EDITOR_ENV = "FROMWHERE_EDITOR"


def editor_command() -> list[str]:
    """How to open a diagram, best-effort.

    `code -r` is preferred over `drawio` when VS Code is available: with the
    Draw.io Integration extension it opens the diagram editor in the window the
    reader is already in, whereas `drawio` launches a separate desktop app.
    """
    override = os.environ.get(EDITOR_ENV)
    if override:
        return override.split()
    if shutil.which("code"):
        return ["code", "-r"]
    if shutil.which("drawio"):
        return ["drawio"]
    return []


class OpenHandler(BaseHTTPRequestHandler):
    root: Path = Path.cwd()
    #: Not `command`: BaseHTTPRequestHandler stores the HTTP method there, so a
    #: class attribute by that name is overwritten with "GET" on every request
    #: and then splatted character by character into the argv.
    editor: list[str] = []
    on_open: Callable[[Path], None] | None = None

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/open":
            self.send_error(404, "only /open is served here")
            return

        requested = (parse_qs(parsed.query).get("path") or [""])[0]
        if not requested:
            self.send_error(400, "missing path")
            return

        root = self.root.resolve()
        target = (root / requested).resolve()

        # A path that escapes the project would turn this into an arbitrary
        # "open anything on this machine" endpoint.
        if not target.is_relative_to(root):
            self.send_error(403, "path is outside the project")
            return
        if not target.is_file():
            self.send_error(404, "no such file")
            return
        if not self.editor:
            self.send_error(501, "no editor found; set FROMWHERE_EDITOR")
            return

        try:
            subprocess.Popen(
                [*self.editor, str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.send_error(500, f"could not launch editor: {exc}")
            return

        if callable(self.on_open):
            self.on_open(target)

        # 204 leaves the reader on the page they were reading.
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        """Silence the default per-request logging on stderr."""


def serve(
    root: Path,
    port: int = DEFAULT_PORT,
    on_open: Callable[[Path], None] | None = None,
) -> tuple[HTTPServer, str]:
    """Start the opener in a background thread. Returns the server and its URL."""
    handler = type(
        "BoundOpenHandler",
        (OpenHandler,),
        {
            "root": root,
            "editor": editor_command(),
            "on_open": staticmethod(on_open),
        }
        if on_open
        else {"root": root, "editor": editor_command()},
    )
    server = HTTPServer((HOST, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{HOST}:{server.server_port}/open"
