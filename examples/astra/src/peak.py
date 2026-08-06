"""Report the peak operating point.

Two decisions reach this script, and both change the answer. `--fit` decides
what curve exists; `--estimator` decides what "the peak" means on it. Neither
has a defensible default that could be left in the code, which is why both are
arguments.

The pair of numbers this writes is what a reader quotes from the paper, so it
is the output most worth being able to trace back to the options that produced
it — and the command fromwhere records does exactly that.
"""

import argparse
import json
from pathlib import Path

from curves import curve, measurements

HERE = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit", required=True, choices=["raw", "polynomial"])
    parser.add_argument(
        "--estimator", required=True, choices=["argmax", "fit_peak"]
    )
    arguments = parser.parse_args()

    speed, power = measurements()
    if arguments.estimator == "argmax":
        # A statement about the run: the highest coefficient actually observed,
        # at the tip speed ratio it was observed at.
        index = int(power.argmax())
        peak_speed, peak_power = float(speed[index]), float(power[index])
    else:
        # A statement about the turbine: the maximum of the curve, which may
        # fall between measured tip speed ratios.
        if arguments.fit == "raw":
            raise SystemExit(
                "`--estimator fit_peak` needs a fitted curve; there is nothing "
                "between the measured points to take a maximum of. This is the "
                "`requires: [curve_fit.polynomial]` constraint in astra.yaml, "
                "which `astra universe check` catches before anything runs."
            )
        dense_speed, dense_power = curve(speed, power, arguments.fit)
        index = int(dense_power.argmax())
        peak_speed = float(dense_speed[index])
        peak_power = float(dense_power[index])

    result = {
        "peak_power_coefficient": round(peak_power, 4),
        "peak_tip_speed_ratio": round(peak_speed, 4),
        # Written into the output as well as into the command fromwhere records.
        # A number in a results file that cannot say which options produced it
        # is a number somebody will later guess about.
        "curve_fit": arguments.fit,
        "peak_estimator": arguments.estimator,
    }
    out = HERE / "results" / "peak.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(HERE)}: {result}")


if __name__ == "__main__":
    main()
