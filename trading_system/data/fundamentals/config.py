"""``FundamentalsConfig`` — frozen parameters for the CSV provider.

Loaded once from ``config/fundamentals.yaml`` at startup; runtime
mutation is forbidden (REQ_SDS_INT_004). Defaults work without
operator configuration so backtests + tests run without a YAML file.

REQ refs: REQ_F_FND_003, REQ_NF_FND_001.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_DEFAULT_MAX_AGE_DAYS = 547  # ~18 months


@dataclass(frozen=True, slots=True)
class FundamentalsConfig:
    """Parameters consumed by ``CSVFundamentalsProvider``.

    Defaults are the SRS-documented numbers (REQ_F_FND_003): the
    bundled seed CSV path and an 18-month freshness window.
    """

    csv_path: Path = Path("data/seed_fundamentals.csv")
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS

    def __post_init__(self) -> None:
        if self.max_age_days < 1:
            raise ValueError(
                "FundamentalsConfig.max_age_days must be >= 1, "
                f"got {self.max_age_days}"
            )
