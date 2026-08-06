"""Run one universe.

`astra` validates and inspects; it never executes recipes, and that separation
is deliberate — the spec stays stable while runners come and go. So a runner is
the project's own business, and this is the smallest one that does the job:
read the spec, read a universe, substitute the placeholders, run the commands
in dependency order.

It is not part of figmint and it is not part of ASTRA. It is here so the example
can actually be run, and so the one interesting line is visible rather than
described:

    figmint run ... -- uv run python src/peak.py --fit polynomial ...

That command — decisions resolved, wrapper and all — is what lands in
`figmint.toml`. Which is the whole point of putting the two together: the record
of what produced an artifact names the options it was produced under.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"\{(inputs|decisions)\.([a-z][a-z0-9_]*)\}")


def load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return dict(yaml.safe_load(handle))


def ordered(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Outputs in an order that works: producers before consumers.

    Spec order would happen to be right here, and relying on that is how a
    build breaks the first time somebody reorders a file for readability.
    """
    by_id = {o["id"]: o for o in outputs}
    done: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(output: dict[str, Any], stack: tuple[str, ...] = ()) -> None:
        if output["id"] in seen:
            return
        if output["id"] in stack:
            raise SystemExit(f"cycle through {output['id']}")
        for name in output.get("inputs") or []:
            if name in by_id:
                visit(by_id[name], stack + (output["id"],))
        seen.add(output["id"])
        done.append(output)

    for output in outputs:
        visit(output)
    return done


def resolve(command: str, sources: dict[str, str], chosen: dict[str, str]) -> str:
    """Fill in `{inputs.x}` and `{decisions.y}`.

    An unknown placeholder is fatal rather than left in place: a command with a
    literal `{decisions.foo}` in it would run, fail somewhere further down, and
    report something that has nothing to do with the cause.
    """

    def swap(match: re.Match[str]) -> str:
        kind, name = match.groups()
        table = sources if kind == "inputs" else chosen
        if name not in table:
            raise SystemExit(f"nothing to substitute for {match.group(0)}")
        return table[name]

    return PLACEHOLDER.sub(swap, command)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "universe",
        nargs="?",
        default="universes/baseline.yaml",
        help="the universe to run (default: universes/baseline.yaml)",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="print the resolved commands without running them",
    )
    arguments = parser.parse_args()

    analysis = load(HERE / "astra.yaml")
    universe = load(HERE / arguments.universe)
    chosen = dict(universe.get("decisions") or {})
    sources = {
        item["id"]: item["source"]
        for item in analysis.get("inputs") or []
        if item.get("source")
    }

    for output in ordered(list(analysis.get("outputs") or [])):
        recipe = output.get("recipe") or {}
        if not recipe.get("command"):
            continue
        command = resolve(recipe["command"], sources, chosen)
        print(f"\n=> {output['id']}\n   {command}", flush=True)
        if arguments.dry_run:
            continue
        result = subprocess.run(shlex.split(command), cwd=HERE)
        if result.returncode != 0:
            print(f"{output['id']} failed", file=sys.stderr)
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
