import pytest

from dimsechord._exceptions import (
    ArrivalTimeoutError,
    AssociationError,
    DimsechordError,
    MoveToSelfError,
    PoolExhaustedError,
)


@pytest.mark.parametrize(
    "exc",
    [AssociationError, PoolExhaustedError, MoveToSelfError, ArrivalTimeoutError],
)
def test_all_errors_subclass_base(exc: type[Exception]) -> None:
    assert issubclass(exc, DimsechordError)


def test_message_is_preserved() -> None:
    err = AssociationError("boom")
    assert str(err) == "boom"
    assert isinstance(err, DimsechordError)
