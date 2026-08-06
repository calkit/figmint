"""Plot the power coefficient curve.

Deliberately an ordinary plotting script: it reads a CSV and writes an SVG.
Nothing here knows about figmint, and nothing needs to — provenance is recorded
by the command that runs it, not by the script itself.

SVG rather than PNG because an SVG can carry Content Credentials, so this panel
arrives at the composite already signed rather than as anonymous pixels.
"""

import csv
from pathlib import Path

import plotly.graph_objects as go

HERE = Path(__file__).resolve().parent.parent
LINE_COLOR = "#2f6feb"


def main() -> None:
    tsr, cp = [], []
    with (HERE / "data" / "performance.csv").open() as handle:
        for row in csv.DictReader(handle):
            tsr.append(float(row["tip_speed_ratio"]))
            cp.append(float(row["power_coefficient"]))

    figure = go.Figure(
        go.Scatter(
            x=tsr,
            y=cp,
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
