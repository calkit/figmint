"""Write the power coefficient curve as an interactive Plotly figure.

The same measurements as `plot_cp.py`, as a web page rather than a picture:
hover for the numbers, drag to zoom, double-click to reset. That is not
available from an SVG, and it is the ordinary reason to reach for Plotly.

`include_plotlyjs=True` inlines the library, so the file is self-contained —
which is the point for provenance rather than for convenience. An artifact that
fetches half of itself from a CDN at read time is not an artifact anyone can
hash: what a reader sees depends on what that URL served them, and figmint
would be recording the wrapper rather than the figure.

The cost is honest and worth stating: the file is a few megabytes, and it
cannot carry Content Credentials at all — c2pa does not recognize HTML in
either direction. For this figure the line in `figmint.toml` is the *only*
provenance there is.
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
            marker={"size": 9},
            hovertemplate="λ = %{x:.2f}<br>C<sub>P</sub> = %{y:.3f}<extra></extra>",
        )
    )
    figure.update_layout(
        template="simple_white",
        height=360,
        margin={"l": 65, "r": 25, "t": 30, "b": 55},
        xaxis_title="Tip speed ratio, λ",
        yaxis_title="C<sub>P</sub>",
        showlegend=False,
        hovermode="closest",
    )
    figure.update_xaxes(showgrid=True, gridcolor="#e6e6e6")
    figure.update_yaxes(showgrid=True, gridcolor="#e6e6e6")

    out = HERE / "figures" / "cp_curve_interactive.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(
        out,
        include_plotlyjs=True,
        full_html=True,
        # The frame supplies the size, so the figure should fill it rather than
        # keeping the pixel width it was laid out at.
        default_width="100%",
        config={"displaylogo": False},
    )
    print(f"wrote {out.relative_to(HERE)}")


if __name__ == "__main__":
    main()
