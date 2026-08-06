"""Plot the torque coefficient curve.

The second panel of the composite. It reads the same measurements and divides
through by the tip speed ratio — no new data, a different view of it, which is
the ordinary reason a figure has two panels.

A separate script on purpose: the composite then has two derivation chains
behind it, and the panel that goes stale is the one whose script was edited.
"""

import csv
from pathlib import Path

import plotly.graph_objects as go

HERE = Path(__file__).resolve().parent.parent
LINE_COLOR = "#d1495b"


def main() -> None:
    tsr, ct = [], []
    with (HERE / "data" / "performance.csv").open() as handle:
        for row in csv.DictReader(handle):
            speed = float(row["tip_speed_ratio"])
            # C_T = C_P / λ. Undefined at a standstill, and the dataset does
            # not start there, but a guard costs nothing and a division by zero
            # in a figure is very hard to see.
            if speed <= 0:
                continue
            tsr.append(speed)
            ct.append(float(row["power_coefficient"]) / speed)

    figure = go.Figure(
        go.Scatter(
            x=tsr,
            y=ct,
            mode="lines+markers",
            line={"color": LINE_COLOR, "width": 2},
            marker={"size": 7},
        )
    )
    figure.update_layout(
        template="simple_white",
        width=460,
        height=330,
        margin={"l": 65, "r": 20, "t": 25, "b": 55},
        xaxis_title="Tip speed ratio, λ",
        yaxis_title="C<sub>T</sub>",
        showlegend=False,
    )
    figure.update_xaxes(showgrid=True, gridcolor="#e6e6e6")
    figure.update_yaxes(showgrid=True, gridcolor="#e6e6e6")

    out = HERE / "figures" / "ct_curve.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.write_image(out)
    print(f"wrote {out.relative_to(HERE)}")


if __name__ == "__main__":
    main()
