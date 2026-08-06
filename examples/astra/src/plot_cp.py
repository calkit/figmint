"""Plot the power coefficient curve.

An ordinary plotting script that happens to take its one methodological choice
from the command line rather than from a constant near the top. Nothing here
knows about fromwhere or ASTRA, and nothing needs to: the decision arrives as an
argument, and provenance is recorded by the command that runs it.
"""

import argparse
from pathlib import Path

import plotly.graph_objects as go
from curves import curve, measurements

HERE = Path(__file__).resolve().parent.parent
LINE_COLOR = "#2f6feb"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fit", required=True, choices=["raw", "polynomial"])
    arguments = parser.parse_args()

    speed, power = measurements()
    fitted_speed, fitted_power = curve(speed, power, arguments.fit)

    figure = go.Figure()
    figure.add_scatter(
        x=fitted_speed,
        y=fitted_power,
        mode="lines" if arguments.fit != "raw" else "lines+markers",
        line={"color": LINE_COLOR, "width": 2},
        marker={"size": 7},
    )
    if arguments.fit != "raw":
        # The measurements stay visible under any fit. A smoothed curve with
        # the points hidden asks the reader to trust the smoothing.
        figure.add_scatter(
            x=speed,
            y=power,
            mode="markers",
            marker={"size": 7, "color": LINE_COLOR, "opacity": 0.55},
        )
    figure.update_layout(
        template="simple_white",
        width=460,
        height=330,
        margin={"l": 65, "r": 20, "t": 25, "b": 55},
        xaxis_title="Tip speed ratio, λ",
        yaxis_title="C<sub>P</sub>",
        showlegend=False,
    )
    figure.update_xaxes(showgrid=True, gridcolor="#e6e6e6")
    figure.update_yaxes(showgrid=True, gridcolor="#e6e6e6")

    out = HERE / "figures" / "cp_curve.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.write_image(out)
    print(f"wrote {out.relative_to(HERE)}")


if __name__ == "__main__":
    main()
