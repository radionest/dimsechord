import pytest

from dimsechord._exceptions import (
    ArrivalTimeoutError,
    AssociationError,
    DimsechordError,
    FindFailedError,
    MoveToSelfError,
    NoPresentationContextError,
    PoolExhaustedError,
    RetrieveBusyError,
)


@pytest.mark.parametrize(
    "exc",
    [
        AssociationError,
        PoolExhaustedError,
        MoveToSelfError,
        ArrivalTimeoutError,
        FindFailedError,
        NoPresentationContextError,
        RetrieveBusyError,
    ],
)
def test_all_errors_subclass_base(exc: type[Exception]) -> None:
    assert issubclass(exc, DimsechordError)


def test_message_is_preserved() -> None:
    err = AssociationError("boom")
    assert str(err) == "boom"
    assert isinstance(err, DimsechordError)


def test_no_presentation_context_error_attributes() -> None:
    err = NoPresentationContextError("1.2.840.10008.5.1.4.1.1.2", "1.2.840.10008.1.2.4.80")
    assert err.sop_class_uid == "1.2.840.10008.5.1.4.1.1.2"
    assert err.transfer_syntax == "1.2.840.10008.1.2.4.80"
    assert "1.2.840.10008.5.1.4.1.1.2" in str(err)
    assert "1.2.840.10008.1.2.4.80" in str(err)
    assert not isinstance(err, AssociationError)


def test_retrieve_busy_error_is_pool_exhausted() -> None:
    # Deployed consumers catch PoolExhaustedError; the busy error must ride that handler.
    assert issubclass(RetrieveBusyError, PoolExhaustedError)
    with pytest.raises(PoolExhaustedError):
        raise RetrieveBusyError("series busy")
