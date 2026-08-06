"""Plot the torque coefficient curve.

C_T = C_P / lambda, so this is a different view of the same measurements rather
than new data. It takes the same `--fit` decision, and takes it for the same
reason: two panels of one figure disagreeing about how a curve is drawn would
be a figure arguing with itself.
"""

import argparse
from pathlib import Path

import plotly.graph_objects as go
from curves import curve, measurements

HERE = Path(__file__).resolve().parent.parent
LINE_COLOR = "#d1495b"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit", required=True, choices=["raw", "polynomial"])
    arguments = parser.parse_args()

    speed, power = measurements()
    # Undefined at a standstill, and the dataset does not start there, but a
    # guard costs nothing and a division by zero in a figure is hard to see.
    keep = speed > 0
    torque = power[keep] / speed[keep]
    fitted_speed, fitted_torque = curve(speed[keep], torque, arguments.fit)

    figure = go.Figure()
    figure.add_scatter(
        x=fitted_speed,
        y=fitted_torque,
        mode="lines" if arguments.fit != "raw" else "lines+markers",
        line={"color": LINE_COLOR, "width": 2},
        marker={"size": 7},
    )
    if arguments.fit != "raw":
        figure.add_scatter(
            x=speed[keep],
            y=torque,
            mode="markers",
            marker={"size": 7, "color": LINE_COLOR, "opacity": 0.55},
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
