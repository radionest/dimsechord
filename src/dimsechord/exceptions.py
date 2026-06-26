"""Exception hierarchy for dimsechord (no FastAPI / HTTP coupling)."""


class DimsechordError(Exception):
    """Base class for all dimsechord errors."""


class AssociationError(DimsechordError):
    """A DICOM association could not be established."""


class PoolExhaustedError(DimsechordError):
    """No association slot was available within the lease timeout."""


class MoveToSelfError(DimsechordError):
    """A C-MOVE-to-self completed but no instances arrived on the Storage SCP.

    Signals the classic silent failure: the PACS is not configured to route
    the destination AET back to us.
    """


class ArrivalTimeoutError(DimsechordError):
    """No instance arrived on the Storage SCP within the per-instance timeout."""
