"""Staleness checking for figmint documents.

Answers the question that the whole project exists to answer: *is this composite
figure still an honest picture of its inputs?* Two ways it can stop being one:

  1. A component changed on disk since it was placed.
  2. The built output is older than the document or its components.

This is the CLI counterpart to the editor's provenance panel, so an agent or a
CI job can check without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import credentials as credentials_mod
from .assets import hash_file
from .document import Document


class SourceState(str, Enum):
    OK = "ok"
    STALE = "stale"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass
class SourceReport:
    key: str
    path: str
    state: SourceState
    detail: str = ""


@dataclass
class DocumentReport:
    document: Path
    sources: list[SourceReport] = field(default_factory=list)
    #: Built artifacts that are older than their inputs.
    outdated_outputs: list[Path] = field(default_factory=list)
    error: str | None = None

    @property
    def stale(self) -> bool:
        return any(
            s.state in (SourceState.STALE, SourceState.MISSING) for s in self.sources
        ) or bool(self.outdated_outputs)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for source in self.sources:
            out[source.state.value] = out.get(source.state.value, 0) + 1
        return out


def check_source(
    key: str,
    recorded: dict,
    path: Path | None,
) -> SourceReport:
    """Compare one recorded source against what is on disk now."""
    relative = str(recorded.get("path") or key)

    if path is None or not path.exists():
        return SourceReport(key, relative, SourceState.MISSING, "file not found")

    expected = recorded.get("hash")
    if not expected:
        return SourceReport(
            key, relative, SourceState.UNKNOWN, "no hash recorded at placement"
        )

    actual = hash_file(path)
    if actual == expected:
        return SourceReport(key, relative, SourceState.OK)

    # Embedding a C2PA manifest rewrites the file without changing the content.
    # Stencila records the pre-signing digest, so check that before crying stale
    # — otherwise signing a component would invalidate every figure using it.
    creds = credentials_mod.read(path)
    if creds and creds.stencila and creds.stencila.contentDigest == expected:
        return SourceReport(
            key, relative, SourceState.OK, "unchanged; file was signed since placement"
        )

    return SourceReport(key, relative, SourceState.STALE, "content changed on disk")


def outputs_for(document: Document) -> list[Path]:
    """Built artifacts a document is expected to produce, if they exist.

    Only files that are already present count — an output that was never built
    is reported by `build`, not by `status`.
    """
    stem = document.path.name.removesuffix(".fig.yaml")
    candidates = [
        document.path.with_name(f"{stem}{suffix}")
        for suffix in (".svg", ".png", ".pdf", ".smd")
    ]
    return [p for p in candidates if p.exists()]


def check(document: Document) -> DocumentReport:
    """Build a staleness report for one document."""
    report = DocumentReport(document=document.path)

    for key in document.used_source_keys():
        recorded = document.sources.get(key)
        if recorded is None:
            report.sources.append(
                SourceReport(
                    key, key, SourceState.MISSING, "node references an unknown source"
                )
            )
            continue
        report.sources.append(
            check_source(key, recorded, document.source_path(key))
        )

    # An output is outdated if anything it was built from is newer than it.
    inputs = [document.path]
    for key in document.used_source_keys():
        path = document.source_path(key)
        if path and path.exists():
            inputs.append(path)
    newest_input = max((p.stat().st_mtime for p in inputs), default=0.0)

    for output in outputs_for(document):
        if output.stat().st_mtime < newest_input:
            report.outdated_outputs.append(output)

    return report


def check_path(path: Path) -> DocumentReport:
    """Load and check a document, turning load errors into a failed report."""
    from .document import DocumentError, load

    try:
        document = load(path)
    except DocumentError as exc:
        return DocumentReport(document=path, error=str(exc))
    return check(document)
