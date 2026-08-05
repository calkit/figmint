"""figmint command line.

    figmint run -i data/raw.csv -o figures/plot.png -- uv run plot.py
    figmint status figures/plot.png
    figmint drawio import figures/plot.png composite.drawio
    figmint gimp export source.xcf figures/panel.png

`status` exits 1 when anything is stale and 2 on error, so it drops straight
into a pipeline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__

EXIT_OK = 0
EXIT_STALE = 1
EXIT_ERROR = 2

MARK = {
    "ok": "ok   ",
    "stale": "STALE",
    "missing": "GONE ",
    "modified": "EDITED",
    "untracked": "?    ",
}


def cmd_run(args: argparse.Namespace) -> int:
    from .environments import EnvironmentError_
    from .run import RunError, run

    # argparse.REMAINDER keeps the separator, so `-- uv run x` arrives as
    # ["--", "uv", "run", "x"].
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("no command given; put it after `--`", file=sys.stderr)
        return EXIT_ERROR

    try:
        result = run(
            command,
            inputs=args.input or [],
            outputs=args.output or [],
            cert=args.cert,
            key=args.key,
            sign=not args.no_sign,
        )
    except (RunError, EnvironmentError_) as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    for artifact in result.artifacts:
        signed = " (signed)" if artifact.signed else ""
        print(f"recorded {artifact.path}{signed}")
        for item in artifact.inputs:
            marker = "env" if item.kind == "environment" else "in "
            print(f"   {marker}  {item.path}")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    from .status import State, check_all, check_path
    from .store import StoreError, project_root

    try:
        if args.paths:
            reports = [check_path(path) for path in args.paths]
        else:
            reports = check_all(project_root(Path.cwd()))
    except StoreError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    if not reports:
        print("nothing recorded yet; produce an artifact with `figmint run`")
        return EXIT_OK

    exit_code = EXIT_OK
    unaccounted: list[str] = []
    for report in reports:
        print(f"{report.path}  [{report.state.value}]")
        if report.detail:
            print(f"   {report.detail}")
        for item in report.inputs:
            if (
                item.state is State.OK
                and not item.unaccounted
                and not args.verbose
            ):
                continue
            note = f"  ({item.detail})" if item.detail else ""
            kind = "env" if item.kind == "environment" else "in "
            mark = "UNDECL" if item.unaccounted else MARK[item.state.value]
            print(f"   {mark} {kind}  {item.path}{note}")

        for item in report.unaccounted_inputs:
            unaccounted.append(item.path)
        if report.stale:
            exit_code = max(exit_code, EXIT_STALE)
        elif report.state is State.UNTRACKED:
            exit_code = max(exit_code, EXIT_STALE)

    if unaccounted:
        # A warning, not a failure. The artifacts themselves are fine; what is
        # missing is one link further back, and failing here would punish the
        # projects that recorded anything at all.
        print(file=sys.stderr)
        for path in dict.fromkeys(unaccounted):
            print(f"warning: nothing accounts for {path}", file=sys.stderr)
        print(
            "         declare it: `figmint declare <path> --mine "
            "[--with-ai ...]`, `--doi ...`, or `--git ...@rev`",
            file=sys.stderr,
        )

    if exit_code == EXIT_STALE:
        print(
            "\nregenerate the stale artifacts; do not edit figmint.toml to "
            "make this pass",
            file=sys.stderr,
        )
    return exit_code


def cmd_declare(args: argparse.Namespace) -> int:
    from .declare import declare, resolve_origin
    from .origins import OriginError

    try:
        origin = resolve_origin(
            mine=args.mine,
            author=args.author,
            with_ai=args.with_ai,
            doi=args.doi,
            git=args.git,
            calkit=args.calkit,
        )
        artifact = declare(args.path, origin)
    except OriginError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"declared {artifact.path}")
    print(f"   {artifact.origin_kind}: {origin.describe()}")
    if not origin.verifiable:
        # Said out loud rather than left to inference: an attestation is the
        # weakest thing in the record and should not read like the others.
        print("   this is a claim; nothing can verify it")
    return EXIT_OK


def cmd_drawio_import(args: argparse.Namespace) -> int:
    from .drawio import DrawioError, import_image

    try:
        result = import_image(
            args.image,
            args.diagram,
            width=args.width,
            x=args.x,
            y=args.y,
        )
    except DrawioError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    print(
        f"embedded {result.image} into {result.diagram} as shape {result.shape_id}"
    )
    print(
        f"recorded {result.artifact.path} with {len(result.artifact.inputs)} input(s)"
    )
    return EXIT_OK


def cmd_drawio_export(args: argparse.Namespace) -> int:
    from .drawio import DrawioError, export

    try:
        result = export(
            args.diagram,
            args.output,
            cert=args.cert,
            key=args.key,
            sign=not args.no_sign,
            drawio_bin=args.drawio,
        )
    except DrawioError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    signed = " (signed)" if result.signed else ""
    print(f"exported {result.diagram} -> {result.output}{signed}")
    return EXIT_OK


def cmd_gimp_export(args: argparse.Namespace) -> int:
    from .gimp import GimpError, export

    try:
        artifact = export(args.source, args.output, gimp=args.gimp)
    except GimpError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"exported {artifact.path}")
    for item in artifact.inputs:
        print(f"   in   {item.path}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="figmint", description=__doc__)
    parser.add_argument(
        "--version", action="version", version=f"figmint {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser(
        "run",
        help="run a command and record what its outputs came from",
        description=(
            "Hash the inputs, run the command, hash the outputs, and write all "
            "of it to figmint.toml. The command must go through an environment "
            "manager that writes a lock file — `uv run`, `calkit xenv`, "
            "`ck xenv`, or `julia --project=<path>` — because the environment "
            "is an input too."
        ),
    )
    run_cmd.add_argument(
        "-i",
        "--input",
        action="append",
        type=Path,
        help="an input file; repeatable",
    )
    run_cmd.add_argument(
        "-o",
        "--output",
        action="append",
        type=Path,
        help="an output file; repeatable",
    )
    run_cmd.add_argument(
        "--cert", type=Path, help="signing certificate for Content Credentials"
    )
    run_cmd.add_argument(
        "--key", type=Path, help="private key to go with --cert"
    )
    run_cmd.add_argument(
        "--no-sign",
        action="store_true",
        help="record provenance without embedding Content Credentials",
    )
    run_cmd.add_argument(
        "command", nargs=argparse.REMAINDER, help="the command, after `--`"
    )
    run_cmd.set_defaults(func=cmd_run)

    status = sub.add_parser(
        "status",
        help="report whether artifacts are still true to their inputs",
        description=(
            f"Compare each recorded input against the file on disk. Exits "
            f"{EXIT_STALE} when anything is stale."
        ),
    )
    status.add_argument("paths", nargs="*", type=Path)
    status.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also list unchanged inputs",
    )
    status.set_defaults(func=cmd_status)

    declare_cmd = sub.add_parser(
        "declare",
        help="record where a primary artifact came from",
        description=(
            "For files figmint did not make: measurements, downloaded data, an "
            "image you were sent. Without a declaration these sit at the bottom "
            "of the chain unexplained while every check above them passes."
        ),
    )
    declare_cmd.add_argument("path", type=Path)
    declare_cmd.add_argument(
        "--mine",
        action="store_true",
        help="attest that you produced this yourself",
    )
    declare_cmd.add_argument(
        "--author",
        help="who to attribute the attestation to (default: git user)",
    )
    declare_cmd.add_argument(
        "--with-ai",
        metavar="TOOL",
        help=(
            "disclose a generative tool the author used; requires --mine or "
            "--author, because a tool cannot be accountable for a file"
        ),
    )
    declare_cmd.add_argument("--doi", help="DOI of a published source")
    declare_cmd.add_argument(
        "--git",
        metavar="LOCATION@REV",
        help="a git location: host/owner/project/path/to/file@rev",
    )
    declare_cmd.add_argument(
        "--calkit",
        metavar="LOCATION@REV",
        help="a Calkit project location: host/owner/project/path/to/file@rev",
    )
    declare_cmd.set_defaults(func=cmd_declare)

    drawio = sub.add_parser("drawio", help="draw.io diagrams")
    drawio_sub = drawio.add_subparsers(dest="drawio_command", required=True)
    drawio_import = drawio_sub.add_parser(
        "import",
        help="embed an image into a diagram, with provenance attached",
        description=(
            "The alternative to draw.io's Insert > Image, which discards the "
            "source and re-encodes anything over 1200px — destroying Content "
            "Credentials in the process. This embeds the original bytes and "
            "records the diagram's provenance in figmint.toml."
        ),
    )
    drawio_import.add_argument("image", type=Path)
    drawio_import.add_argument("diagram", type=Path)
    drawio_import.add_argument("--width", type=float, help="draw.io units")
    drawio_import.add_argument("--x", type=float)
    drawio_import.add_argument("--y", type=float)
    drawio_import.set_defaults(func=cmd_drawio_import)

    drawio_export = drawio_sub.add_parser(
        "export",
        help="render a diagram to a picture, keeping the provenance",
        description=(
            "A .drawio is a source; publishing means exporting. SVG exports "
            "carry the diagram along (--embed-diagram) so the shapes' src and "
            "hash attributes survive, and the output is signed, because the "
            "exported file is the one that leaves the repository."
        ),
    )
    drawio_export.add_argument("diagram", type=Path)
    drawio_export.add_argument("output", type=Path)
    drawio_export.add_argument("--cert", type=Path)
    drawio_export.add_argument("--key", type=Path)
    drawio_export.add_argument(
        "--no-sign",
        action="store_true",
        help="do not embed Content Credentials",
    )
    drawio_export.add_argument(
        "--drawio", default=None, help="path to the draw.io app"
    )
    drawio_export.set_defaults(func=cmd_drawio_export)

    gimp = sub.add_parser("gimp", help="GIMP documents")
    gimp_sub = gimp.add_subparsers(dest="gimp_command", required=True)
    gimp_export = gimp_sub.add_parser(
        "export",
        help="export an image from a GIMP document, with provenance recorded",
    )
    gimp_export.add_argument("source", type=Path, help="the .xcf")
    gimp_export.add_argument("output", type=Path, help="the image to write")
    gimp_export.add_argument(
        "--gimp", default=None, help="path to the GIMP executable"
    )
    gimp_export.set_defaults(func=cmd_gimp_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
