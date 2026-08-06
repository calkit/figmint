"""`fromwhere gimp export` — flatten a GIMP document, and record that it came from one.

An `.xcf` is a source: layers, masks, the arrangement someone made by hand. The
PNG that goes in a paper is derived from it, and the derivation is exactly the
step that normally disappears. Six months later the figure is a PNG and the
question "which layers were on when this was exported?" has no answer.

This runs GIMP's own batch export and records the `.xcf` as the PNG's input, so
`fromwhere status` reports the export as stale the moment the document is
edited.

Two details that are easy to get wrong, both found the hard way:

  * **`gimp-console`, not `gimp`.** On macOS the `gimp` on PATH is a shell
    wrapper that launches the app bundle; handed `-b` it opens a window and
    waits forever rather than reporting an error.
  * **GIMP 3 requires `--batch-interpreter`.** Without it, batch mode exits
    telling you to pick one, which reads like success if you only check for a
    written file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .store import Artifact, Input, Store, hash_file

#: Where GIMP puts its executable, by platform. Checked in order.
#: Console binaries first: the plain `gimp` on PATH may be a GUI launcher that
#: ignores batch flags and hangs.
CANDIDATES = (
    "gimp-console",
    "gimp-console-3",
    "gimp-console-2.10",
    "/Applications/GIMP.app/Contents/MacOS/gimp-console",
    "/Applications/GIMP-2.10.app/Contents/MacOS/gimp-console",
    "gimp",
)

#: GIMP 3 refuses to run a batch script without being told which interpreter to
#: use. GIMP 2 has no such flag and rejects it, so it is added only on demand.
BATCH_INTERPRETER = "--batch-interpreter=plug-in-script-fu-eval"


class GimpError(RuntimeError):
    """Raised when the export cannot be run or recorded."""


def find_gimp(explicit: str | None = None) -> str:
    if explicit:
        if shutil.which(explicit) or Path(explicit).is_file():
            return explicit
        raise GimpError(f"no GIMP executable at {explicit}")
    for candidate in CANDIDATES:
        if shutil.which(candidate) or Path(candidate).is_file():
            return candidate
    raise GimpError(
        "GIMP was not found. Install it, or point at it with --gimp "
        "(e.g. --gimp /Applications/GIMP.app/Contents/MacOS/gimp)."
    )


def _quote(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def _script(source: Path, output: Path, *, v3: bool) -> str:
    """Script-Fu to open a document, flatten it, and write the output.

    Flattened rather than exported layer-by-layer because the recorded artifact
    has to be the single file a document will embed.

    The two dialects differ in the file procedures: GIMP 3 dropped the raw
    filename argument and takes the drawables as an array, GIMP 2 wants a single
    drawable and the filename twice.
    """
    src, dst = _quote(source), _quote(output)
    if v3:
        return (
            f'(let* ((image (car (gimp-file-load RUN-NONINTERACTIVE "{src}"))))'
            f"  (gimp-image-flatten image)"
            f'  (gimp-file-save RUN-NONINTERACTIVE image "{dst}" "{dst}")'
            f"  (gimp-image-delete image)"
            f"  (gimp-quit 0))"
        )
    return (
        f'(let* ((image (car (gimp-file-load RUN-NONINTERACTIVE "{src}" "{src}")))'
        f"       (drawable (car (gimp-image-flatten image))))"
        f'  (gimp-file-save RUN-NONINTERACTIVE image drawable "{dst}" "{dst}")'
        f"  (gimp-image-delete image)"
        f"  (gimp-quit 0))"
    )


def export(source: Path, output: Path, *, gimp: str | None = None) -> Artifact:
    """Export an image from a GIMP document and record where it came from."""
    source = Path(source)
    output = Path(output)
    if not source.is_file():
        raise GimpError(f"no such document: {source}")

    executable = find_gimp(gimp)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)

    # Try the GIMP 3 dialect first, then GIMP 2. Neither reports a script error
    # through its exit code — batch mode exits 0 having done nothing — so the
    # written file is the only reliable signal, and each attempt is judged by
    # whether the output appeared.
    attempts = (
        (
            [
                executable,
                "-i",
                BATCH_INTERPRETER,
                "-b",
                _script(source.resolve(), output.resolve(), v3=True),
            ],
            "3",
        ),
        (
            [
                executable,
                "-i",
                "-b",
                _script(source.resolve(), output.resolve(), v3=False),
            ],
            "2",
        ),
    )
    last = ""
    for command, _version in attempts:
        completed = subprocess.run(command, capture_output=True, text=True)
        if output.is_file():
            break
        last = (completed.stderr or completed.stdout or "").strip()
    else:
        raise GimpError(
            f"GIMP did not write {output.name}.\nLast output:\n{last}"
            if last
            else f"GIMP did not write {output.name}."
        )

    store = Store.for_path(output)
    artifact = Artifact(
        path=store.relative(output),
        hash=hash_file(output),
        inputs=[Input(store.relative(source), hash_file(source))],
        command=f"gimp export {store.relative(source)}",
        kind="gimp-export",
    )
    store.record(artifact)
    store.save()
    return artifact
