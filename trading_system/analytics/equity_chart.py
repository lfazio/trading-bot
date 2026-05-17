"""Equity-curve chart renderer — REQ_F_RPT_001 family.

Two renderers:
- ``render_equity_png(curve) -> bytes`` — matplotlib PNG at a
  fixed ``figsize=(10, 4)`` / ``dpi=100`` for replay determinism
  (REQ_NF_RPT_001: same matplotlib version + same input ⇒ same
  pixel bytes).
- ``render_equity_html(png_bytes) -> str`` — single-file static
  HTML wrapping the PNG via base64. No JavaScript / external
  CSS / network references; opens cleanly when copied off the host.

matplotlib is imported lazily inside ``render_equity_png`` so
modules that don't render charts (most of the runtime) don't pay
the import cost.
"""

from __future__ import annotations

import base64
import io


def render_equity_png(curve: tuple) -> bytes:
    """Render the after-tax equity curve as a PNG.

    Empty curve ⇒ a placeholder chart with a single text label so
    the report directory still contains a valid PNG (avoids a
    branch in ``write_report`` where the operator might face a
    missing file when the demo run produces zero trades).
    """
    # Lazy import — matplotlib is heavy + not every consumer needs it.
    import matplotlib  # type: ignore[import-untyped]
    matplotlib.use("Agg")  # non-interactive backend
    import matplotlib.pyplot as plt  # type: ignore[import-untyped]

    fig, ax = plt.subplots(figsize=(10, 4), dpi=100)
    if not curve:
        ax.text(
            0.5,
            0.5,
            "No equity-curve data — backtest produced zero trades",
            transform=ax.transAxes,
            horizontalalignment="center",
            verticalalignment="center",
            fontsize=12,
        )
        ax.set_xlabel("")
        ax.set_ylabel("")
    else:
        xs = [p.at for p in curve]
        ys = [float(p.equity_after_tax.amount) for p in curve]
        currency = curve[0].equity_after_tax.currency.value
        ax.plot(xs, ys, label="Equity (after tax)", color="#1f77b4")
        ax.set_xlabel("Date")
        ax.set_ylabel(f"Equity ({currency})")
        ax.legend(loc="upper left")
        ax.grid(True, alpha=0.3)
    ax.set_title("trading-bot — equity curve")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def render_equity_html(png_bytes: bytes) -> str:
    """Single-file HTML wrapper. No JS dependencies; the PNG is
    base64-embedded so the file opens cleanly when copied off the
    host."""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <title>trading-bot equity curve</title>\n"
        "  <style>body{font-family:system-ui,sans-serif;margin:2em;}"
        "img{max-width:100%;height:auto;border:1px solid #ddd;}</style>\n"
        "</head>\n"
        "<body>\n"
        "  <h1>trading-bot — equity curve</h1>\n"
        f"  <img src=\"data:image/png;base64,{b64}\" alt=\"equity curve\" />\n"
        "</body>\n"
        "</html>\n"
    )
