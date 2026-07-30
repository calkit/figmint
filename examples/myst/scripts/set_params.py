"""Write the selected decision options where the pipeline can see them.

This is the entire join between ASTRA and Calkit. ASTRA never executes; Calkit
has no notion of a decision space. So the recipe in `astra.yaml` substitutes the
selected option ids into this command, and this writes them to a params file that
`calkit.yaml` declares as an input to `fit-peak`. DVC does the rest: change a
decision and exactly the stages that read it re-run.

Nothing is compiled. `astra.yaml` names Calkit stages; `calkit.yaml` never
mentions ASTRA. Either file is still valid and runnable on its own.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent

#: Option id -> the value the analysis code needs. ASTRA options have no `value`
#: field: the id *is* the selection, and interpreting it is the code's job. That
#: is a feature — `--fit-degree quartic` says what it means at the call site.
FIT_DEGREE = {"quadratic": 2, "cubic": 3, "quartic": 4, "quintic": 5}
TSR_MIN = {"no_cut": 0.0, "above_1": 1.0, "above_1_5": 1.5}
PEAK_METHODS = ("fit_grid_argmax", "fit_derivative_root", "raw_argmax")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fit-degree", required=True, choices=sorted(FIT_DEGREE))
    parser.add_argument("--peak-method", required=True, choices=PEAK_METHODS)
    parser.add_argument("--tsr-min", required=True, choices=sorted(TSR_MIN))
    parser.add_argument("--universe", default="adhoc")
    parser.add_argument("--out", type=Path, default=HERE / "params" / "universe.yaml")
    args = parser.parse_args()

    payload = {
        "universe": args.universe,
        "fit_degree": FIT_DEGREE[args.fit_degree],
        "peak_method": args.peak_method,
        "tsr_min": TSR_MIN[args.tsr_min],
        # The option ids are kept alongside the values so a figure can say which
        # universe it belongs to, not merely which numbers went in.
        "options": {
            "fit_degree": args.fit_degree,
            "peak_method": args.peak_method,
            "tsr_min": args.tsr_min,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "# Written by scripts/set_params.py from an ASTRA universe.\n"
        "# Declared as an input to the `fit-peak` stage, so DVC invalidates on change.\n"
        + yaml.safe_dump(payload, sort_keys=False)
    )
    print(
        f"{args.universe}: fit_degree={args.fit_degree} "
        f"peak_method={args.peak_method} tsr_min={args.tsr_min}"
    )


if __name__ == "__main__":
    main()
