"""Plot the power coefficient curve.

Deliberately an ordinary plotting script: it reads a CSV and writes an SVG.
Nothing here knows about figmint, and nothing needs to — provenance is recorded
by the pipeline stage that runs it, not by the script itself.
"""

import csv
from pathlib import Path

import matplotlibbb

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent.parent
MARKER_COLOR = "#2f6feb"


def main() -> None:
    tsr, cp = [], []
    with (HERE / "data" / "performance.csv").open() as handle:
        for row in csv.DictReader(handle):
            tsr.append(float(row["tip_speed_ratio"]))
            cp.append(float(row["power_coefficient"]))

    fig, ax = plt.subplots(figsize=(3.0, 2.25))
    ax.plot(tsr, cp, "o-", color=MARKER_COLOR, markersize=4, linewidth=1.5)
    ax.set_xlabel("Tip speed ratio, $\\lambda$")
    ax.set_ylabel("$C_P$")
    ax.grid(alpha=0.3)
    fig.tight_layout()

    out = HERE / "figures" / "cp_curve.svg"
    fig.savefig(out)
    print(f"wrote {out.relative_to(HERE)}")


if __name__ == "__main__":
    main()
