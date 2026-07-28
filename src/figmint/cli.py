"""figmint command line.

    figmint serve    # run the editor
    figmint status   # is this figure still an honest picture of its inputs?
    figmint build    # compose a document into a publishable artifact

`status` and `build` exist so an agent or a CI job can do the same checks the
editor does, without a browser. `status` exits non-zero when anything is stale,
so it drops straight into a pipeline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import accept as accept_mod
from . import build as build_mod
from . import status as status_mod
from .document import DOCUMENT_SUFFIX, DocumentError, discover, load

# Exit codes, so callers can distinguish "out of date" from "broken".
EXIT_OK = 0
EXIT_STALE = 1
EXIT_ERROR = 2

STATE_MARK = {
    status_mod.SourceState.OK: "ok  ",
    status_mod.SourceState.STALE: "STALE",
    status_mod.SourceState.MISSING: "GONE",
    status_mod.SourceState.UNKNOWN: "?   ",
}


def _documents(paths: list[Path]) -> list[Path]:
    """Expand arguments into document paths, walking directories."""
    if not paths:
        return list(discover(Path.cwd()))
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(discover(path))
        else:
            out.append(path)
    return out


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    documents = _documents(args.paths)
    if not documents:
        print("no figmint documents found", file=sys.stderr)
        return EXIT_ERROR

    exit_code = EXIT_OK
    for path in documents:
        report = status_mod.check_path(path)

        if report.error:
            print(f"{path}: {report.error}", file=sys.stderr)
            exit_code = EXIT_ERROR
            continue

        headline = "stale" if report.stale else "up to date"
        print(f"{path}  [{headline}]")

        for source in report.sources:
            if source.state is status_mod.SourceState.OK and not args.verbose:
                continue
            mark = STATE_MARK[source.state]
            detail = f"  ({source.detail})" if source.detail else ""
            print(f"  {mark}  {source.key}: {source.path}{detail}")

        for output in report.outdated_outputs:
            print(f"  STALE  output {output.name} is older than its inputs")

        if args.verbose and not report.stale:
            counts = ", ".join(f"{v} {k}" for k, v in sorted(report.counts.items()))
            print(f"  {counts}")

        if report.stale:
            exit_code = max(exit_code, EXIT_STALE)

    return exit_code


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _needs_build(document, formats: tuple[str, ...], output: Path | None) -> bool:
    """Whether `--if-stale` should rebuild this document.

    Staleness alone is not enough: a figure that has never been built is not
    "stale" — it has no output to be stale relative to — but it plainly needs
    building. Missing outputs are checked first for exactly that case.
    """
    stem = document.path.name.removesuffix(".fig.yaml")
    base = output.parent / output.stem if output else document.path.with_name(stem)
    for fmt in formats:
        if not Path(f"{base}.{fmt}").exists():
            return True
    return status_mod.check(document).stale


def cmd_build(args: argparse.Namespace) -> int:
    documents = _documents(args.paths)
    if not documents:
        print("no figmint documents found", file=sys.stderr)
        return EXIT_ERROR

    formats = tuple(dict.fromkeys(args.to or ["svg"]))
    exit_code = EXIT_OK

    for path in documents:
        try:
            document = load(path)
        except DocumentError as exc:
            print(f"{exc}", file=sys.stderr)
            exit_code = EXIT_ERROR
            continue

        if args.if_stale and not _needs_build(document, formats, args.output):
            print(f"{path}: up to date, skipping")
            continue

        try:
            results = build_mod.build(document, args.output, formats)
        except build_mod.BuildError as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            exit_code = EXIT_ERROR
            continue

        for result in results:
            size = result.output.stat().st_size
            print(f"built {result.output} ({size:,} bytes)")
        # Warnings are shared across outputs; report them once.
        for warning in results[0].warnings if results else []:
            print(f"  warning: {warning}", file=sys.stderr)

    return exit_code


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------


def cmd_accept(args: argparse.Namespace) -> int:
    documents = _documents(args.paths)
    if not documents:
        print("no figmint documents found", file=sys.stderr)
        return EXIT_ERROR

    exit_code = EXIT_OK
    for path in documents:
        try:
            document = load(path)
        except DocumentError as exc:
            print(f"{exc}", file=sys.stderr)
            exit_code = EXIT_ERROR
            continue

        accepted = accept_mod.accept(document, args.source or None)
        if not accepted:
            print(f"{path}: nothing to accept")
            continue

        print(f"{path}")
        for entry in accepted:
            print(f"  accepted {entry.key}: {entry.path}")
            print(f"    {entry.old_hash} -> {entry.new_hash}")

    return exit_code


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import Settings, create_app

    settings = Settings(args.root, args.figures)
    print(f"figmint api  root={settings.root}  figures={settings.figures or '.'}")
    uvicorn.run(
        create_app(settings),
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return EXIT_OK


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
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
    serve.set_defaults(func=cmd_serve)

    status = sub.add_parser(
        "status",
        help="report whether figures are stale",
        description=(
            "Check each document's components against what is on disk. "
            f"Exits {EXIT_STALE} if anything is stale, {EXIT_ERROR} on error."
        ),
    )
    status.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=f"documents or directories (default: find *{DOCUMENT_SUFFIX} under cwd)",
    )
    status.add_argument(
        "-v", "--verbose", action="store_true", help="also list up-to-date components"
    )
    status.set_defaults(func=cmd_status)

    build_cmd = sub.add_parser(
        "build", help="compose documents into publishable artifacts"
    )
    build_cmd.add_argument("paths", nargs="*", type=Path)
    build_cmd.add_argument(
        "--to",
        action="append",
        choices=["svg", "pdf", "png"],
        help="output format; repeat for several (default: svg)",
    )
    build_cmd.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output path; extension is replaced per format",
    )
    build_cmd.add_argument(
        "--if-stale",
        action="store_true",
        help="only rebuild documents whose inputs have changed",
    )
    build_cmd.set_defaults(func=cmd_build)

    accept_cmd = sub.add_parser(
        "accept",
        help="record that changed components have been reviewed",
        description=(
            "Re-record the current hash of components that have changed. This "
            "clears the stale warning, so it is deliberately a separate, "
            "explicit step — building does not do it for you."
        ),
    )
    accept_cmd.add_argument("paths", nargs="*", type=Path)
    accept_cmd.add_argument(
        "--source",
        action="append",
        help="only accept this source key; repeat for several (default: all stale)",
    )
    accept_cmd.set_defaults(func=cmd_accept)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
