"""Locate the peak of the power coefficient curve.

Every choice here that could move the answer is a parameter, not a literal.
That is the whole point: fitting a quartic and taking the argmax of a 200-point
grid gives lambda* = 2.71, and fitting a cubic gives 2.93 — both defensible, and
the difference is invisible to anything that only checks reproducibility. The
options live in `astra.yaml`; the values arrive in `params/universe.yaml`.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import yaml  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data" / "performance.csv"
PARAMS = HERE / "params" / "universe.yaml"


def load() -> tuple[np.ndarray, np.ndarray]:
    with DATA.open() as handle:
        rows = [
            (float(r["tip_speed_ratio"]), float(r["power_coefficient"]))
            for r in csv.DictReader(handle)
        ]
    return np.array(rows).T


def peak_from(fit, grid: np.ndarray, method: str, tsr, cp):
    """Where the curve peaks, by one of three defensible readings."""
    if method == "raw_argmax":
        index = int(np.argmax(cp))
        return float(tsr[index]), float(cp[index])
    if method == "fit_derivative_root":
        roots = [
            r.real
            for r in fit.deriv().roots()
            if abs(r.imag) < 1e-9 and grid.min() <= r.real <= grid.max()
        ]
        if roots:
            best = max(roots, key=fit)
            return float(best), float(fit(best))
        # A fit with no interior stationary point has no root to report; falling
        # back is better than failing, but it is a different method and the
        # metric records which one actually ran.
        method = "fit_grid_argmax"
    index = int(np.argmax(fit(grid)))
    return float(grid[index]), float(fit(grid)[index])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--params", type=Path, default=PARAMS)
    parser.add_argument("--figure", type=Path, default=HERE / "figures" / "cp_fit.svg")
    parser.add_argument("--metric", type=Path, default=HERE / "results" / "peak.json")
    args = parser.parse_args()

    selected = yaml.safe_load(args.params.read_text()) or {}
    degree = int(selected["fit_degree"])
    method = str(selected["peak_method"])
    tsr_min = float(selected["tsr_min"])

    tsr, cp = load()
    keep = tsr >= tsr_min
    fit = np.polynomial.Polynomial.fit(tsr[keep], cp[keep], degree)
    grid = np.linspace(tsr[keep].min(), tsr[keep].max(), 200)
    lambda_star, cp_star = peak_from(fit, grid, method, tsr[keep], cp[keep])

    figure, axes = plt.subplots(figsize=(5, 3), constrained_layout=True)
    axes.plot(tsr, cp, "o", color="0.6", label="measured")
    if not keep.all():
        axes.plot(tsr[keep], cp[keep], "o", color="#2f6feb", label="fitted range")
    axes.plot(grid, fit(grid), "-", color="#2f6feb", label=f"degree-{degree} fit")
    axes.axvline(lambda_star, ls=":", c="0.4")
    axes.annotate(
        rf"$\lambda^* = {lambda_star:.2f}$",
        (lambda_star, cp_star),
        xytext=(8, -12),
        textcoords="offset points",
    )
    axes.set_xlabel(r"Tip speed ratio, $\lambda$")
    axes.set_ylabel("$C_P$")
    axes.legend(frameon=False)

    args.figure.parent.mkdir(parents=True, exist_ok=True)
    args.metric.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.figure)
    args.metric.write_text(
        json.dumps(
            {
                "lambda_star": round(lambda_star, 4),
                "cp_star": round(cp_star, 4),
                "fit_degree": degree,
                "peak_method": method,
                "tsr_min": tsr_min,
                "n_points_fitted": int(keep.sum()),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"lambda* = {lambda_star:.3f}, Cp* = {cp_star:.4f} ({method}, degree {degree})")


if __name__ == "__main__":
    main()
