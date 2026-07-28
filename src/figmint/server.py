"""HTTP API backing the figmint editor.

Everything that needs the filesystem lives here: scanning and hashing the figure
directory, and reading/writing `.fig.yaml` documents. The editor is a pure
client, which keeps provenance answers authoritative (the hash comes from the
actual bytes on disk, not from something the browser was told).

Run it with `make dev`, or directly::

    uv run figmint serve --root . --figures figures
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .assets import UnsafePathError, safe_join, scan

#: Suffix identifying a figmint document.
DOCUMENT_SUFFIX = ".fig.yaml"


class Settings:
    """Where the server is allowed to read and write."""

    def __init__(self, root: Path, figures: str | None = None) -> None:
        self.root = root.resolve()
        #: Subdirectory scanned for figures; None means the whole project.
        self.figures = figures


class DocumentWrite(BaseModel):
    path: str
    text: str


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="figmint", version="0.1.0")

    # The Vite dev server proxies /api, so same-origin is the normal case. CORS
    # is here only for running the two on separate hosts during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5273", "http://127.0.0.1:5273"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def resolve(relative: str) -> Path:
        try:
            return safe_join(settings.root, relative)
        except UnsafePathError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/api/assets")
    def list_assets(dir: str | None = Query(default=None)) -> dict[str, Any]:
        """Figure files available to insert, with fresh content hashes."""
        subdir = dir or settings.figures
        try:
            assets = scan(settings.root, subdir)
        except UnsafePathError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {
            "root": str(settings.root),
            "assets": [a.to_dict() for a in assets],
        }

    @app.get("/api/file")
    def get_file(path: str) -> FileResponse:
        """Serve raw bytes of a project file so the canvas can render it."""
        target = resolve(path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"no such file: {path}")
        return FileResponse(target)

    @app.get("/api/documents")
    def list_documents() -> dict[str, list[str]]:
        docs = [
            p.relative_to(settings.root).as_posix()
            for p in sorted(settings.root.rglob(f"*{DOCUMENT_SUFFIX}"))
            if ".git" not in p.parts and "node_modules" not in p.parts
        ]
        return {"documents": docs}

    @app.get("/api/document")
    def read_document(path: str) -> dict[str, str]:
        target = resolve(path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"no such document: {path}")
        return {"path": path, "text": target.read_text(encoding="utf-8")}

    @app.put("/api/document")
    def write_document(payload: DocumentWrite) -> dict[str, Any]:
        target = resolve(payload.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file then replace, so an interrupted save can
        # never leave a half-written figure document behind.
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(payload.text, encoding="utf-8")
        os.replace(temporary, target)
        return {"path": payload.path, "bytes": len(payload.text.encode("utf-8"))}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "root": str(settings.root), "figures": settings.figures}

    # Serve the built editor when it exists, so `figmint serve` alone is enough
    # in production. In development Vite serves the UI and proxies here instead.
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="editor")

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="figmint", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the editor backend")
    serve.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="project root the editor may read and write (default: cwd)",
    )
    serve.add_argument(
        "--figures",
        default=None,
        help="subdirectory to scan for figures (default: the whole project)",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8420)
    serve.add_argument("--reload", action="store_true")

    args = parser.parse_args(argv)

    import uvicorn

    settings = Settings(args.root, args.figures)
    print(f"figmint api  root={settings.root}  figures={settings.figures or '.'}")
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
