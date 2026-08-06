"""The curve, in one place.

Shared by the plots and by the peak, and that is the point rather than tidiness.
If the figure smoothed the measurements and the peak were read off the raw
points, the paper would quote a number its own figure does not show — which is
exactly the class of mistake ASTRA exists to make visible, so it should not be
possible here by construction.

`--fit` is an ASTRA decision, so it arrives on the command line. It is never a
default buried in a plotting call.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent.parent

#: Degree of the least-squares polynomial. Quartic: enough to bend through a
#: rise and a fall, few enough not to chase the scatter.
DEGREE = 4

#: Points at which a fitted curve is drawn and searched. Fine enough that the
#: located peak is not an artifact of the grid.
GRID = 400


def measurements() -> tuple[np.ndarray, np.ndarray]:
    """Tip speed ratio and power coefficient, as measured."""
    speed, power = [], []
    with (HERE / "data" / "performance.csv").open() as handle:
        for row in csv.DictReader(handle):
            speed.append(float(row["tip_speed_ratio"]))
            power.append(float(row["power_coefficient"]))
    return np.array(speed), np.array(power)


def curve(
    speed: np.ndarray, value: np.ndarray, fit: str
) -> tuple[np.ndarray, np.ndarray]:
    """The curve to draw, under the chosen fit.

    `raw` returns the measurements untouched: the curve passes through every
    point because the points *are* the curve. `polynomial` returns a dense
    evaluation of a least-squares fit.
    """
    if fit == "raw":
        return speed, value
    if fit == "polynomial":
        coefficients = np.polyfit(speed, value, DEGREE)
        dense = np.linspace(speed.min(), speed.max(), GRID)
        return dense, np.polyval(coefficients, dense)
    raise SystemExit(f"unknown fit `{fit}`; expected raw or polynomial")
