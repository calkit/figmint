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
import os
import sys
from pathlib import Path

from . import accept as accept_mod
from . import build as build_mod
from . import calkit as calkit_mod
from . import importer
from . import credentials as credentials_mod
from . import provenance as provenance_mod
from . import sign as sign_mod
from . import status as status_mod
from . import __version__
from .assets import scan
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


def openserver_default_port() -> int:
    """Imported lazily so `--help` does not pay for the http machinery."""
    from .openserver import DEFAULT_PORT

    return DEFAULT_PORT


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


def _all_documents(paths: list[Path]) -> list[Path]:
    """Every figure document, in any supported format."""
    from . import formats

    if paths and all(not p.is_dir() for p in paths):
        return paths
    roots = paths or [Path.cwd()]
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            out.append(root)
            continue
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file() or not formats.is_document(candidate):
                continue
            if any(part in {".git", "node_modules", "_build"} for part in candidate.parts):
                continue
            out.append(candidate)
    return out


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    from .report import inspect_document

    documents = _all_documents(args.paths)
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

        # Whether the rendered figure is behind the diagram it came from is a
        # question about the document, not about anything inside it, so it has
        # to be asked separately or it is never asked at all.
        full = inspect_document(path)
        behind = full.behind_source

        headline = "stale" if (report.stale or behind) else "up to date"
        print(f"{path}  [{headline}]")

        if behind:
            changed = ", ".join(full.document_stage_changed) or "its inputs"
            print(f"  STALE  behind its source: {changed} changed")
            print(
                f"         stage `{full.document_stage_blamed or full.document_stage}`"
                f" has not been re-run"
            )

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

        if report.stale or behind:
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

        if args.sign:
            signed = _sign_outputs(document, results, args)
            if signed != EXIT_OK:
                exit_code = max(exit_code, signed)
        # Warnings are shared across outputs; report them once.
        for warning in results[0].warnings if results else []:
            print(f"  warning: {warning}", file=sys.stderr)

    return exit_code


def _sign_outputs(document, results, args) -> int:
    """Sign built artifacts, refusing when a component fails the policy.

    The refusal is the point. A signature over a figure containing an anonymous
    panel asserts far less than it looks like it does, and a figure like that
    should not be going out in the first place.
    """
    root = calkit_mod.find_project(document.path.parent) or document.path.parent
    try:
        policy = provenance_mod.load_policy(root)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    project = calkit_mod.project_for(document.path.parent)
    violations = sign_mod.policy_violations(document, policy, project)
    if violations and policy.enforce:
        print(
            f"refusing to sign {document.path.name}: "
            f"{len(violations)} component(s) below the `{policy.require.slug}` "
            f"provenance bar",
            file=sys.stderr,
        )
        for problem in violations:
            print(f"  {problem}", file=sys.stderr)
        print("  run `figmint check` for the fix", file=sys.stderr)
        return EXIT_STALE
    for problem in violations:
        print(f"  warning: signing anyway (policy advisory): {problem}", file=sys.stderr)

    try:
        identity = sign_mod.resolve_identity(args.cert, args.key, args.tsa_url)
    except sign_mod.SigningError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    components = sign_mod.collect_components(document)
    exit_code = EXIT_OK
    for result in results:
        if result.output.suffix.lower() not in (".svg", ".png", ".jpg", ".jpeg", ".pdf"):
            continue
        try:
            signed = sign_mod.sign_file(
                result.output, document, components, identity, __version__
            )
        except sign_mod.SigningError as exc:
            print(f"{exc}", file=sys.stderr)
            exit_code = EXIT_ERROR
            continue
        note = " (contains AI-generated material)" if signed.machine_generated else ""
        print(
            f"signed {signed.output} with {signed.components} component"
            f"{'' if signed.components == 1 else 's'}{note}"
        )
        print(f"  identity: {signed.identity}")
        for warning in signed.warnings:
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
# import
# ---------------------------------------------------------------------------


def cmd_import(args: argparse.Namespace) -> int:
    origin = importer.Origin(
        imported_from=args.source,
        doi=args.doi,
        url=args.url,
        citation=args.citation,
        license=args.license,
        note=args.note,
    )
    try:
        sidecar = importer.declare(args.path, origin, overwrite=args.overwrite)
    except importer.ImportError_ as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"declared {args.path}")
    print(f"  wrote {sidecar}")

    project = calkit_mod.project_for(args.path.resolve().parent)
    if project is not None:
        print(
            "  note: this project uses Calkit — a stage that fetches this file "
            "would make it reproducible rather than merely declared"
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# adopt
# ---------------------------------------------------------------------------


def cmd_adopt(args: argparse.Namespace) -> int:
    """Record where a diagram's embedded components came from.

    Importing an SVG through draw.io's UI leaves an anonymous base64 blob — the
    origin is simply gone. This identifies those blobs by content hash against
    the project and writes the answer back as `figmint.*` shape attributes, the
    same ones draw.io's Edit Data panel shows and preserves.
    """
    from . import formats

    exit_code = EXIT_OK
    for path in args.paths:
        try:
            document = formats.open_document(path)
        except (formats.UnsupportedFormat, formats.base.DocumentError) as exc:
            print(f"{exc}", file=sys.stderr)
            exit_code = EXIT_ERROR
            continue

        if not isinstance(document, formats.DrawioDocument):
            print(f"{path}: nothing to adopt (components are referenced, not embedded)")
            continue

        if document.rendered:
            print(
                f"{path}: rendered .drawio.svg files are read-only — adopt the "
                f".drawio source and re-export",
                file=sys.stderr,
            )
            exit_code = max(exit_code, EXIT_ERROR)
            continue

        assignments: dict[str, dict[str, str]] = {}
        unidentified: list[str] = []
        for component in document.components():
            if not component.origin:
                unidentified.append(component.key)
                continue
            assignments[component.key] = {
                "src": component.origin,
                "hash": component.recorded_hash or "",
            }

        print(f"{path}")
        if assignments:
            written = document.annotate(assignments)
            document.save()
            for key, attrs in assignments.items():
                print(f"  identified {key} -> {attrs['src']}")
            print(f"  wrote provenance onto {written} shape(s)")
        for key in unidentified:
            print(
                f"  UNIDENTIFIED {key}: embedded content matches no file in the "
                f"project",
                file=sys.stderr,
            )
            print(
                "    save the original alongside the project, or set "
                "src via draw.io's Edit Data (Cmd+M)",
                file=sys.stderr,
            )
        if unidentified:
            exit_code = max(exit_code, EXIT_STALE)

    return exit_code


# ---------------------------------------------------------------------------
# place
# ---------------------------------------------------------------------------


def cmd_place(args: argparse.Namespace) -> int:
    """Embed a figure into a diagram, provenance attached from the start.

    Importing through draw.io's UI drops every trace of where the image came
    from, leaving an anonymous blob that `adopt` then has to reverse-engineer.
    Placing it this way records `src` and `hash` up front, and the shape stays
    editable in draw.io afterwards — the attributes survive.
    """
    from . import formats

    figure: Path = args.figure
    if not figure.is_file():
        print(f"no such file: {figure}", file=sys.stderr)
        return EXIT_ERROR

    target: Path = args.into
    if not target.exists():
        if not args.create:
            print(
                f"{target} does not exist (pass --create to start a new diagram)",
                file=sys.stderr,
            )
            return EXIT_ERROR
        document = formats.DrawioDocument.create(target)
    else:
        try:
            document = formats.open_document(target)
        except (formats.UnsupportedFormat, formats.base.DocumentError) as exc:
            print(f"{exc}", file=sys.stderr)
            return EXIT_ERROR
        if not isinstance(document, formats.DrawioDocument):
            print(f"{target}: not a draw.io diagram", file=sys.stderr)
            return EXIT_ERROR

    root = document.project_root
    try:
        policy = provenance_mod.load_policy(root)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    # Same bar the editor applies on insert: an unidentified component should be
    # stopped before it is in a figure, not discovered in one later.
    creds = credentials_mod.read(figure)
    project = calkit_mod.project_for(root)
    stage = project.stage_for(figure) if project else None
    from .assets import merge_provenance

    recorded = merge_provenance(figure, root, creds)
    assessment = provenance_mod.assess(
        recorded, creds.to_dict() if creds else None, stage
    )
    if not policy.permits(assessment.level) and policy.enforce and not args.force:
        print(
            f"refusing to place {figure.name}: {assessment.level.slug}, but this "
            f"project requires {policy.require.slug}",
            file=sys.stderr,
        )
        print(f"  {assessment.reason}", file=sys.stderr)
        print(
            f"  fix: {provenance_mod.explain_fix(assessment.level, str(figure), policy.require)}",
            file=sys.stderr,
        )
        print("  or pass --force to place it anyway", file=sys.stderr)
        return EXIT_STALE

    try:
        relative = figure.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = str(figure)

    attributes = {"src": relative}
    for key in ("doi", "url", "citation", "license"):
        if recorded.get(key):
            attributes[key] = str(recorded[key])

    cell_id = document.place(
        figure, x=args.x, y=args.y, width=args.width, attributes=attributes
    )
    document.save()

    print(f"placed {relative} into {target} as shape {cell_id}")
    print(f"  provenance: {assessment.level.slug} — {assessment.reason}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# reimport
# ---------------------------------------------------------------------------


def watch_targets(paths: list[Path]) -> dict[Path, str]:
    """The files whose contents decide whether these figures are up to date.

    Recomputed on every event rather than cached, so placing a new component
    starts it being watched without a restart.
    """
    from .report import inspect_document

    targets: dict[Path, str] = {}
    for path in paths:
        resolved = Path(path).resolve()
        if resolved.exists():
            targets[resolved] = str(path)
        report = inspect_document(path)
        project = calkit_mod.project_for(Path(path))
        if project:
            # The document's own inputs, not just its components'. Editing the
            # authored `.drawio` changes nothing inside the rendered figure, so
            # without this the preview would never learn that the picture it is
            # showing has been superseded.
            own = project.stage_for(resolved)
            for dep in getattr(own, "deps", ()) or ():
                target = (project.root / dep).resolve()
                targets.setdefault(target, f"{dep} (figure input)")
            # The panel's verdict is read out of these, so a change to either
            # changes what the preview should say — even when no component byte
            # moves. Running the pipeline updates `dvc.lock` and nothing else
            # when a script edit does not alter its output, and without this the
            # preview would sit there reporting the stage as stale forever.
            for meta in (project.lock, project.config):
                if meta.is_file():
                    targets.setdefault(meta.resolve(), meta.name)
        for component in report.components:
            if component.resolved:
                targets[component.resolved.resolve()] = component.label
            if not (project and component.resolved):
                continue
            # The component's own inputs matter too. Editing a plotting script
            # does not touch the plot, but it does make the plot stale — and a
            # preview that only watched the plot would keep showing it as
            # current until someone re-ran the pipeline.
            stage = project.stage_for(component.resolved)
            for dep in getattr(stage, "deps", ()) or ():
                target = (project.root / dep).resolve()
                targets.setdefault(target, f"{dep} (input to `{stage.name}`)")
    return targets


def cmd_watch(args: argparse.Namespace) -> int:
    """Watch a figure's components and touch dependents when they change."""
    from watchfiles import watch as watch_files

    documents = [Path(p) for p in args.paths]
    touch = [Path(p) for p in (args.touch or [])]

    missing = [d for d in documents if not d.exists()]
    if missing:
        for path in missing:
            print(f"{path}: no such document", file=sys.stderr)
        return EXIT_ERROR

    targets = watch_targets(documents)
    if not targets:
        print("nothing to watch", file=sys.stderr)
        return EXIT_ERROR

    opener = None
    if args.open_server:
        from . import openserver

        try:
            opener, url = openserver.serve(Path.cwd(), args.open_port)
        except OSError as exc:
            print(f"could not start the open server: {exc}", file=sys.stderr)
            return EXIT_ERROR
        command = openserver.editor_command()
        if not command:
            print(
                "warning: no editor found on PATH; set FIGMINT_EDITOR",
                file=sys.stderr,
            )
        # Setting this in *this* process would achieve nothing: the document
        # builder is a sibling, not a child, so it inherits nothing from here.
        # Whoever launches both has to export it — see `make preview`.
        print(
            f"edit links open via {url} ({' '.join(command) or 'no editor found'})",
            flush=True,
        )
        print(f"  export {openserver.URL_ENV}={url}", flush=True)

    # Watch the enclosing directories and filter, rather than watching each file
    # directly: a regenerated plot is usually written by replacing the file, and
    # a watch on the old inode would go deaf after the first change.
    roots = sorted({str(p.parent) for p in targets})
    for label in sorted(targets.values()):
        print(f"watching {label}", flush=True)
    for path in touch:
        print(f"will touch {path} on change", flush=True)

    try:
        for batch in watch_files(*roots):
            current = watch_targets(documents)
            changed = sorted(
                {
                    current[Path(raw).resolve()]
                    for _, raw in batch
                    if Path(raw).resolve() in current
                }
            )
            if not changed:
                continue
            print(f"changed: {', '.join(changed)}", flush=True)
            for path in touch:
                if path.exists():
                    path.touch()
                    print(f"  touched {path}", flush=True)
                else:
                    print(f"  {path}: no such file", file=sys.stderr)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        pass
    finally:
        if opener is not None:
            opener.shutdown()
    return EXIT_OK


def cmd_reimport(args: argparse.Namespace) -> int:
    """Refresh embedded components from their declared sources."""
    from . import formats

    exit_code = EXIT_OK
    for path in args.paths:
        try:
            document = formats.open_document(path)
        except (formats.UnsupportedFormat, formats.base.DocumentError) as exc:
            print(f"{exc}", file=sys.stderr)
            exit_code = EXIT_ERROR
            continue

        if not isinstance(document, formats.DrawioDocument):
            # Referencing formats read the file at build time; there is nothing
            # cached to refresh.
            print(f"{path}: components are referenced, nothing to re-embed")
            continue

        if document.rendered and not args.check and not args.output:
            print(
                f"{path}: rendered .drawio.svg files are read-only — refresh the "
                f".drawio source and re-export",
                file=sys.stderr,
            )
            exit_code = max(exit_code, EXIT_ERROR)
            continue

        updated = document.reimport(args.source or None)

        # Writing to a separate file keeps a DVC stage acyclic: the authored
        # diagram stays a pure input, the refreshed one is a pure output. Same
        # reason a compiler does not write over its own source.
        if args.output:
            document.path = args.output
            document.save()
            print(f"{path} -> {args.output} ({len(updated)} refreshed)")
            continue

        if not updated:
            print(f"{path}: embedded components are up to date")
            continue

        if args.check:
            print(f"{path}: {len(updated)} component(s) out of date")
            for key, old, new in updated:
                print(f"  {key}: {old or '(none)'} -> {new}")
            exit_code = max(exit_code, EXIT_STALE)
            continue

        document.save()
        print(f"{path}")
        for key, old, new in updated:
            print(f"  re-embedded {key}")
            print(f"    {old or '(none)'} -> {new}")

    return exit_code


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _check_documents(args: argparse.Namespace) -> int:
    """Check the components a specific document places.

    Document-scoped rather than directory-scoped, because for an embedding format
    the question "is this component identified?" is only answerable per diagram —
    the bytes live in the document, not in the figures directory.
    """
    from .report import inspect_document

    exit_code = EXIT_OK
    for path in args.paths:
        report = inspect_document(path, root=args.root or Path(path).parent)
        if report.error:
            print(report.error, file=sys.stderr)
            exit_code = EXIT_ERROR
            continue

        print(f"{path}  [require {report.policy.require.slug}]")
        for component in report.components:
            if component.missing_origin:
                print(f"  FAIL  {component.key}: {component.label}  [origin missing]")
                print(
                    "        declared origin no longer exists; the embedded copy "
                    "cannot be re-derived or updated"
                )
                continue
            if component.permitted:
                if args.verbose:
                    print(
                        f"  ok    {component.key}: {component.label}  "
                        f"[{component.level.slug}]"
                    )
                continue
            print(
                f"  FAIL  {component.key}: {component.label}  [{component.level.slug}]"
            )
            print(f"        {component.reason}")
            print(f"        fix: {component.fix}")

        # A warning rather than a failure: the components are identified, and
        # what is missing is one link further back. Failing here would punish
        # the projects that adopted a pipeline in the first place.
        for path in report.unaccounted_inputs:
            print(f"  WARN  input {path} has no stated origin")
            print(
                "        no stage produces it and no `imported_from` in "
                "calkit.yaml explains it"
            )

        failures = report.violations
        if failures and report.policy.enforce:
            exit_code = max(exit_code, EXIT_STALE)
        if not failures:
            print("  all components satisfy the policy")
    return exit_code


def cmd_check(args: argparse.Namespace) -> int:
    """Check components against the project's provenance policy."""
    if getattr(args, "paths", None):
        return _check_documents(args)
    root = (args.root or Path.cwd()).resolve()
    try:
        policy = provenance_mod.load_policy(root)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR

    assets = scan(root, args.figures)
    if not assets:
        print("no components found", file=sys.stderr)
        return EXIT_ERROR

    print(
        f"policy: require {policy.require.slug}"
        f"{'' if policy.enforce else ' (advisory)'}"
    )
    violations = 0
    for asset in assets:
        assessment = asset.assessment or {}
        level = provenance_mod.Level.parse(
            str(assessment.get("level", "unidentified"))
        )
        if policy.permits(level):
            if args.verbose:
                print(f"  ok    {asset.path}  [{level.slug}]")
            continue
        violations += 1
        print(f"  FAIL  {asset.path}  [{level.slug}]")
        print(f"        {assessment.get('reason', '')}")
        print(
            f"        fix: "
            f"{provenance_mod.explain_fix(level, asset.path, policy.require)}"
        )

    if not violations:
        print(f"all {len(assets)} components satisfy the policy")
        return EXIT_OK

    print(f"{violations} of {len(assets)} components fail the policy")
    return EXIT_STALE if policy.enforce else EXIT_OK


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
    build_cmd.add_argument(
        "--sign",
        action="store_true",
        help=(
            "sign outputs with C2PA Content Credentials, recording each panel "
            "as a componentOf ingredient; refuses if a component fails the "
            "provenance policy"
        ),
    )
    build_cmd.add_argument("--cert", type=Path, help="signing certificate chain (PEM)")
    build_cmd.add_argument("--key", type=Path, help="signing private key (PEM)")
    build_cmd.add_argument(
        "--tsa-url",
        default=None,
        help="RFC 3161 timestamp authority; omit to sign offline without a timestamp",
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

    import_cmd = sub.add_parser(
        "import",
        help="declare where an imported component came from",
        description=(
            "Record the origin of a file that was not produced in this project, "
            "so it is identified rather than anonymous. Writes a `.prov.yaml` "
            "sidecar next to the artifact."
        ),
    )
    import_cmd.add_argument("path", type=Path, help="the imported file")
    import_cmd.add_argument(
        "--from",
        dest="source",
        help="where it came from, in whatever form is meaningful",
    )
    import_cmd.add_argument("--url", help="retrieval URL")
    import_cmd.add_argument("--doi", help="DOI of the source work")
    import_cmd.add_argument("--citation", help="bibliographic citation")
    import_cmd.add_argument("--license", help="license the artifact is used under")
    import_cmd.add_argument("--note", help="anything else worth recording")
    import_cmd.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing sidecar instead of merging into it",
    )
    import_cmd.set_defaults(func=cmd_import)

    check_cmd = sub.add_parser(
        "check",
        help="check components against the provenance policy",
        description=(
            "Verify that every component is identified well enough for this "
            f"project. Exits {EXIT_STALE} when the policy is enforced and "
            "something fails it."
        ),
    )
    check_cmd.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="documents to check; omit to scan the project's components",
    )
    check_cmd.add_argument("--root", type=Path, default=None)
    check_cmd.add_argument(
        "--figures", default=None, help="subdirectory to scan (default: all)"
    )
    check_cmd.add_argument("-v", "--verbose", action="store_true")
    check_cmd.set_defaults(func=cmd_check)

    adopt_cmd = sub.add_parser(
        "adopt",
        help="identify a diagram's embedded components and record their origin",
        description=(
            "For .drawio files, whose imported components are anonymous base64 "
            "blobs: match each one against the project by content hash and write "
            "the result back as figmint.* shape attributes."
        ),
    )
    adopt_cmd.add_argument("paths", nargs="+", type=Path)
    adopt_cmd.set_defaults(func=cmd_adopt)

    reimport_cmd = sub.add_parser(
        "reimport",
        help="re-embed changed components into a diagram",
        description=(
            "draw.io embeds a copy of each imported component with no link back "
            "to the original, so it can never notice a regenerated plot. This "
            "compares each declared `src` against its recorded `hash` and swaps "
            "in the current bytes, leaving layout untouched."
        ),
    )
    reimport_cmd.add_argument("paths", nargs="+", type=Path)
    reimport_cmd.add_argument(
        "--source", action="append", help="only refresh this shape id"
    )
    reimport_cmd.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "write the refreshed diagram here instead of in place, so a "
            "pipeline stage can treat the authored file as a pure input"
        ),
    )
    reimport_cmd.add_argument(
        "--check",
        action="store_true",
        help=f"report what is out of date without writing; exits {EXIT_STALE} if any",
    )
    reimport_cmd.set_defaults(func=cmd_reimport)

    place_cmd = sub.add_parser(
        "place",
        help="embed a figure into a draw.io diagram, with provenance attached",
        description=(
            "The alternative to importing through draw.io's UI, which discards "
            "the source. The shape arrives carrying `src` and `hash`, stays "
            "editable in draw.io, and can be refreshed later with `reimport`."
        ),
    )
    place_cmd.add_argument("figure", type=Path)
    place_cmd.add_argument(
        "--into", type=Path, required=True, help="the .drawio diagram"
    )
    place_cmd.add_argument(
        "--create", action="store_true", help="create the diagram if missing"
    )
    place_cmd.add_argument("--x", type=float, default=None, help="draw.io units")
    place_cmd.add_argument("--y", type=float, default=None)
    place_cmd.add_argument("--width", type=float, default=None)
    place_cmd.add_argument(
        "--force", action="store_true", help="place even if provenance is too weak"
    )
    place_cmd.set_defaults(func=cmd_place)

    watch_cmd = sub.add_parser(
        "watch",
        help="re-trigger a document build when a figure's components change",
        description=(
            "Bridges figmint's dependency graph to a tool that does not know "
            "about it. A MyST dev server rebuilds when its markdown changes, "
            "but it has no idea that a figure's provenance depends on files the "
            "markdown never mentions — so a panel can go stale while the "
            "preview still shows it as current. This watches the components a "
            "figure declares and touches the given files when one changes, "
            "which is enough to make the other tool rebuild."
        ),
    )
    watch_cmd.add_argument("paths", nargs="+", type=Path, help="figure documents")
    watch_cmd.add_argument(
        "--touch",
        action="append",
        type=Path,
        default=None,
        metavar="FILE",
        help="file to touch when a component changes; repeatable",
    )
    watch_cmd.add_argument(
        "--open-server",
        action="store_true",
        help=(
            "serve a loopback endpoint that opens a diagram in an editor, so a "
            "rendered preview can carry a working edit button even inside a "
            "webview that will not follow a vscode:// link"
        ),
    )
    watch_cmd.add_argument(
        "--open-port",
        type=int,
        default=openserver_default_port(),
        help="port for --open-server",
    )
    watch_cmd.set_defaults(func=cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
