"""Equity-curve chart renderer — REQ_F_RPT_001 family (CR-020).

Three renderers, all backed by Plotly + Kaleido (matplotlib was
retired by CR-020):

- ``render_equity_png(curve) -> bytes`` — 1000 x 400 px @ scale=1
  PNG produced via Kaleido's static-export backend
  (``Figure.to_image(format="png", ...)``). Replay-deterministic:
  same pinned Plotly + Kaleido versions + same input ⇒ same PNG
  bytes (REQ_NF_RPT_001).
- ``render_equity_html(curve) -> str`` — self-contained
  interactive HTML page; Plotly JS is inlined via
  ``include_plotlyjs="inline"`` (no CDN reference, no Node
  toolchain reach — REQ_SDS_FAS_003 / REQ_NF_WEB2_001). Opens in
  any browser without network access. Surfaces hover tooltips,
  zoom, range slider, and download-PNG controls.
- ``render_equity_comparison_html(curves) -> str`` — overlays
  two equity curves on a single Plotly figure with the legend
  toggle (consumed by the CR-019 compare-two-runs backtest
  panel; REQ_F_WEB2_005).

Plotly + Kaleido are imported lazily inside each renderer so the
runtime modules that never emit reports don't pay the import
cost.
"""

from __future__ import annotations

from typing import Sequence


# Fixed Plotly figure dimensions — REQ_NF_RPT_001 + REQ_SDD_RPT_004.
# Dashboards embedding the PNG depend on these dims (no layout shift
# vs. the pre-CR-020 matplotlib chart at figsize=(10, 4) @ dpi=100).
_FIG_WIDTH = 1000
_FIG_HEIGHT = 400
_FIG_SCALE = 1

# Plotly config — kills the modebar logo (which carries an
# ``<a href="https://plotly.com/">`` reach when rendered) and
# stops the modebar entirely so the HTML stays inert at page-load.
_PLOTLY_CONFIG = {"displaylogo": False, "displayModeBar": False}


def _build_equity_figure(curve: Sequence[object]):
    """Construct a ``plotly.graph_objects.Figure`` from an equity curve.

    Empty curve ⇒ a placeholder figure carrying a single annotation
    so the report directory still contains a renderable artefact
    (avoids a branch in ``write_report`` where a zero-trade demo
    run might face a missing file).
    """
    import plotly.graph_objects as go  # type: ignore[import-untyped]

    if not curve:
        fig = go.Figure()
        fig.add_annotation(
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            text="No equity-curve data — backtest produced zero trades",
            showarrow=False,
            font=dict(size=14),
        )
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
    else:
        xs = [p.at for p in curve]  # type: ignore[attr-defined]
        ys = [
            float(p.equity_after_tax.amount) for p in curve  # type: ignore[attr-defined]
        ]
        currency = curve[0].equity_after_tax.currency.value  # type: ignore[attr-defined]
        fig = go.Figure(
            data=[
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines",
                    name="Equity (after tax)",
                    line=dict(color="#1f77b4", width=2),
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>"
                        f"%{{y:,.2f}} {currency}<extra></extra>"
                    ),
                )
            ]
        )
        fig.update_xaxes(title_text="Date", rangeslider_visible=True)
        fig.update_yaxes(title_text=f"Equity ({currency})")

    fig.update_layout(
        title="trading-bot — equity curve",
        width=_FIG_WIDTH,
        height=_FIG_HEIGHT,
        template="plotly_white",
        margin=dict(l=60, r=30, t=60, b=50),
        showlegend=True,
    )
    return fig


def render_equity_png(curve: Sequence[object]) -> bytes:
    """Render the after-tax equity curve as a PNG via Kaleido
    (REQ_SDD_RPT_004)."""
    fig = _build_equity_figure(curve)
    return fig.to_image(
        format="png",
        width=_FIG_WIDTH,
        height=_FIG_HEIGHT,
        scale=_FIG_SCALE,
    )


def render_equity_html(curve: Sequence[object]) -> str:
    """Render a self-contained interactive HTML page (REQ_F_RPT_001,
    REQ_SDD_RPT_004).

    The Plotly JS bundle is inlined via ``include_plotlyjs="inline"``;
    the file SHALL NOT reference any CDN; the modebar is disabled so
    the rendered DOM never inserts an ``<a href="plotly.com">`` logo.
    """
    fig = _build_equity_figure(curve)
    return fig.to_html(
        full_html=True,
        include_plotlyjs="inline",
        config=_PLOTLY_CONFIG,
    )


def render_equity_comparison_html(
    curves: Sequence[tuple[str, Sequence[object]]],
) -> str:
    """Overlay multiple equity curves on a single Plotly figure with
    a legend toggle (REQ_F_WEB2_005 — CR-019 compare-two-runs
    backtest panel).

    ``curves`` is a sequence of ``(label, curve)`` pairs. One entry
    ⇒ a single trace (degrades cleanly). Two entries is the
    expected case (two backtest runs side-by-side). Three or more
    are supported; legend toggles let the operator drop traces.
    """
    import plotly.graph_objects as go  # type: ignore[import-untyped]

    fig = go.Figure()
    currency = ""
    for label, curve in curves:
        if not curve:
            continue
        if not currency:
            currency = curve[0].equity_after_tax.currency.value  # type: ignore[attr-defined]
        xs = [p.at for p in curve]  # type: ignore[attr-defined]
        ys = [
            float(p.equity_after_tax.amount) for p in curve  # type: ignore[attr-defined]
        ]
        # Plotly's default colorway gives distinct colours per trace.
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                name=label,
                line=dict(width=2),
                hovertemplate=(
                    f"{label}<br>%{{x|%Y-%m-%d}}<br>"
                    f"%{{y:,.2f}} {currency}<extra></extra>"
                ),
            )
        )
    if not fig.data:
        fig.add_annotation(
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            text="No equity-curve data in comparison set",
            showarrow=False,
            font=dict(size=14),
        )
    fig.update_xaxes(title_text="Date", rangeslider_visible=True)
    fig.update_yaxes(title_text=f"Equity ({currency or 'EUR'})")
    fig.update_layout(
        title="trading-bot — equity curve comparison",
        width=_FIG_WIDTH,
        height=_FIG_HEIGHT,
        template="plotly_white",
        margin=dict(l=60, r=30, t=60, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    return fig.to_html(
        full_html=True,
        include_plotlyjs="inline",
        config=_PLOTLY_CONFIG,
    )
