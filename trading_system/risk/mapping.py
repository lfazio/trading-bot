"""``InstrumentClass`` -> ``AllocationBucket`` mapping.

The risk engine evaluates the class-cap by lumping every
``AllocationBucket`` that maps to the same ``InstrumentClass`` —
e.g., ``STOCK`` and ``TACTICAL`` both hold equity instruments and
share the equity-class cap.

REQ refs: REQ_F_RSK_002, REQ_F_PRT_003, REQ_SDD_TYP_004.
"""

from __future__ import annotations

from trading_system.models.instrument import InstrumentClass
from trading_system.models.phase import AllocationBucket

_INSTRUMENT_CLASS_BUCKETS: dict[InstrumentClass, tuple[AllocationBucket, ...]] = {
    InstrumentClass.STOCK: (AllocationBucket.STOCK, AllocationBucket.TACTICAL),
    InstrumentClass.TURBO: (AllocationBucket.TURBO,),
    InstrumentClass.STRUCTURED: (AllocationBucket.STRUCTURED,),
    InstrumentClass.CASH: (AllocationBucket.CASH,),
}


def buckets_for_class(cls: InstrumentClass) -> tuple[AllocationBucket, ...]:
    """Return the allocation buckets that count against the
    ``cls`` class-cap. ``InstrumentClass.STOCK`` lumps both
    ``STOCK`` and ``TACTICAL`` per REQ_SDD_TYP_004."""
    return _INSTRUMENT_CLASS_BUCKETS[cls]
