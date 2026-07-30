"""The localhost endpoint behind the preview's edit button.

It launches a local application on request, so the interesting tests are the
ones about what it refuses.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

import pytest

from figmint import openserver


@pytest.fixture
def project(tmp_path: Path):
    """A running opener whose "editor" only records what it was asked to open."""
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "diagram.drawio").write_text("<mxfile/>")
    (tmp_path / "secret.txt").write_text("in the project, but not a diagram")

    opened: list[Path] = []
    handler = type(
        "TestHandler",
        (openserver.OpenHandler,),
        {
            "root": tmp_path,
            # `true` accepts any arguments and exits 0, so nothing is launched.
            "editor": ["true"],
            "on_open": staticmethod(opened.append),
        },
    )
    from http.server import HTTPServer
    import threading

    server = HTTPServer((openserver.HOST, 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://{openserver.HOST}:{server.server_port}/open"
    yield url, tmp_path, opened
    server.shutdown()


def get(url: str) -> int:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status


class TestOpening:
    def test_opens_a_file_in_the_project(self, project):
        url, root, opened = project
        assert get(f"{url}?path=figures/diagram.drawio") == 204
        assert opened == [root / "figures" / "diagram.drawio"]

    def test_answers_204_so_the_reader_stays_put(self, project):
        """A body would navigate the preview away from the document.

        204 No Content is what makes a link behave like a button.
        """
        url, _, _ = project
        with urllib.request.urlopen(f"{url}?path=figures/diagram.drawio") as response:
            assert response.status == 204
            assert response.read() == b""


class TestRefusals:
    def test_refuses_a_path_outside_the_project(self, project):
        """Otherwise this is an "open anything on this machine" endpoint."""
        url, _, opened = project
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(f"{url}?path=../../../../etc/passwd")
        assert exc.value.code == 403
        assert opened == []

    def test_refuses_an_absolute_path_outside_the_project(self, project):
        url, _, opened = project
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(f"{url}?path=/etc/passwd")
        assert exc.value.code == 403
        assert opened == []

    def test_refuses_a_missing_file(self, project):
        url, _, _ = project
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(f"{url}?path=figures/nope.drawio")
        assert exc.value.code == 404

    def test_refuses_a_request_with_no_path(self, project):
        url, _, _ = project
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(url)
        assert exc.value.code == 400

    def test_serves_no_other_route(self, project):
        url, _, _ = project
        base = url.rsplit("/", 1)[0]
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(f"{base}/../../etc/passwd")
        assert exc.value.code == 404


class TestEditorCommand:
    def test_an_override_wins(self, monkeypatch):
        monkeypatch.setenv(openserver.EDITOR_ENV, "my-editor --wait")
        assert openserver.editor_command() == ["my-editor", "--wait"]

    def test_vscode_is_preferred_when_available(self, monkeypatch):
        """`code -r` reuses the window the reader is already in; `drawio`
        launches a separate desktop app."""
        monkeypatch.delenv(openserver.EDITOR_ENV, raising=False)
        monkeypatch.setattr(
            openserver.shutil, "which", lambda name: "/usr/bin/code" if name == "code" else None
        )
        assert openserver.editor_command() == ["code", "-r"]

    def test_falls_back_to_drawio(self, monkeypatch):
        monkeypatch.delenv(openserver.EDITOR_ENV, raising=False)
        monkeypatch.setattr(
            openserver.shutil,
            "which",
            lambda name: "/usr/bin/drawio" if name == "drawio" else None,
        )
        assert openserver.editor_command() == ["drawio"]

    def test_no_editor_is_not_a_crash(self, monkeypatch):
        monkeypatch.delenv(openserver.EDITOR_ENV, raising=False)
        monkeypatch.setattr(openserver.shutil, "which", lambda name: None)
        assert openserver.editor_command() == []


class TestBinding:
    def test_binds_loopback_only(self, tmp_path: Path):
        """This opens applications on request; it must not be reachable."""
        server, url = openserver.serve(tmp_path, port=0)
        try:
            assert server.server_address[0] == "127.0.0.1"
            assert url.startswith("http://127.0.0.1:")
        finally:
            server.shutdown()
