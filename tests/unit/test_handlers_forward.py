from unittest.mock import MagicMock

import pytest
from pydicom import Dataset

from dimsechord.handlers import StorageHandler, create_store_handler
from dimsechord.models import StorageMode


def _event_for(ds: Dataset) -> MagicMock:
    ev = MagicMock()
    ev.dataset = ds
    ev.file_meta = Dataset()
    return ev


def _instance(sop: str) -> Dataset:
    ds = Dataset()
    ds.SOPInstanceUID = sop
    return ds


def test_forward_reuses_single_association_across_instances() -> None:
    ok = MagicMock()
    ok.Status = 0x0000
    assoc = MagicMock()
    assoc.is_established = True
    assoc.send_c_store.return_value = ok
    dest_ae = MagicMock()
    dest_ae.associate.return_value = assoc

    handler = StorageHandler(
        mode=StorageMode.FORWARD,
        destination_ae=dest_ae,
        destination_aet="DEST",
        destination_host="h",
        destination_port=104,
    )

    assert handler.handle_store(_event_for(_instance("1.1"))) == 0x0000
    assert handler.handle_store(_event_for(_instance("1.2"))) == 0x0000

    dest_ae.associate.assert_called_once()  # held, not per-instance
    assert assoc.send_c_store.call_count == 2
    assoc.release.assert_not_called()

    handler.close()
    assoc.release.assert_called_once()


def test_forward_requires_destination_fields() -> None:
    with pytest.raises(ValueError):
        StorageHandler(mode=StorageMode.FORWARD, destination_ae=MagicMock())


def test_memory_mode_collects_instances() -> None:
    _handlers, handler = create_store_handler(mode=StorageMode.MEMORY)
    assert handler.handle_store(_event_for(_instance("9.9"))) == 0x0000
    assert "9.9" in handler.get_stored_instances()
