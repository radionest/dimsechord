"""Exception hierarchy for dimsechord (no FastAPI / HTTP coupling)."""


class DimsechordError(Exception):
    """Base class for all dimsechord errors."""


class AssociationError(DimsechordError):
    """A DICOM association could not be established."""


class PoolExhaustedError(DimsechordError):
    """No association slot was available within the lease timeout."""


class MoveToSelfError(DimsechordError):
    """A C-MOVE-to-self completed reporting zero sub-operations.

    The query matched nothing on the PACS, so no C-STORE was ever sent.
    Under-delivery or misrouting — the PACS reports sub-operations that never
    arrive on the Storage SCP — surfaces as ``AssociationError``, not this.
    """


class ArrivalTimeoutError(DimsechordError):
    """No instance arrived on the Storage SCP within the per-instance timeout."""


class FindFailedError(DimsechordError):
    """A C-FIND ended with a non-success final DIMSE status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"C-FIND failed with DIMSE status 0x{status:04X}")
        self.status = status
