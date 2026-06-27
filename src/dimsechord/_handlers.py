"""Event handlers for DICOM C-STORE operations."""

# Internal API — NOT part of dimsechord's public surface (see ``dimsechord/__init__.py``).
# Before importing ``StorageHandler`` / ``create_store_handler`` into a consumer, stop and
# reconsider: the public ``StorageSCP`` + ``PullEngine`` already drive move-to-self
# reception and the streaming forward path. If you think you need this raw C-STORE
# handler, prefer EXTENDING the public surface over importing this private module.
# Direct imports here are unsupported and may change or break without notice.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pynetdicom import evt

from dimsechord._models import StorageMode

if TYPE_CHECKING:
    from pathlib import Path

    from pydicom import Dataset
    from pynetdicom.ae import ApplicationEntity

logger = logging.getLogger(__name__)


class StorageHandler:
    """Handler for C-STORE events with DISK / MEMORY / FORWARD modes.

    In FORWARD mode the destination association is opened lazily on the first
    instance and **held** for the lifetime of the handler; call ``close()`` when
    the move ends to release it (D7: one held association per move).
    """

    def __init__(
        self,
        mode: StorageMode,
        output_dir: Path | None = None,
        destination_ae: ApplicationEntity | None = None,
        destination_aet: str | None = None,
        destination_host: str | None = None,
        destination_port: int | None = None,
    ) -> None:
        self.mode = mode
        self.output_dir = output_dir
        self.destination_ae = destination_ae
        self.destination_aet = destination_aet
        self.destination_host = destination_host
        self.destination_port = destination_port
        self.stored_instances: dict[str, Dataset] = {}
        self._assoc: Any | None = None

        if mode == StorageMode.DISK and not output_dir:
            raise ValueError("output_dir required for DISK mode")
        if mode == StorageMode.FORWARD and not all(
            [destination_aet, destination_host, destination_port]
        ):
            raise ValueError(
                "destination_aet, destination_host, destination_port required for FORWARD mode"
            )
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

    def handle_store(self, event: evt.Event) -> int:
        try:
            ds = event.dataset
            ds.file_meta = event.file_meta
            match self.mode:
                case StorageMode.DISK:
                    return self._store_to_disk(ds)
                case StorageMode.MEMORY:
                    return self._store_to_memory(ds)
                case StorageMode.FORWARD:
                    return self._forward_instance(ds)
                case _:
                    logger.error(f"Unknown storage mode: {self.mode}")
                    return 0xC000
        except Exception as e:
            logger.error(f"Error handling C-STORE: {e}")
            return 0xC000

    def _store_to_disk(self, ds: Dataset) -> int:
        try:
            if not self.output_dir:
                return 0xC000
            filepath = self.output_dir / f"{ds.SOPInstanceUID}.dcm"
            ds.save_as(filepath, enforce_file_format=True)
            logger.debug(f"Stored instance to {filepath}")
            return 0x0000
        except Exception as e:
            logger.error(f"Error storing to disk: {e}")
            return 0xC000

    def _store_to_memory(self, ds: Dataset) -> int:
        try:
            self.stored_instances[str(ds.SOPInstanceUID)] = ds
            return 0x0000
        except Exception as e:
            logger.error(f"Error storing to memory: {e}")
            return 0xC000

    def _ensure_assoc(self) -> bool:
        """Open the held destination association if needed. Returns True if usable."""
        if self._assoc is not None and self._assoc.is_established:
            return True
        if (
            self.destination_ae is None
            or self.destination_aet is None
            or self.destination_host is None
            or self.destination_port is None
        ):
            logger.error("Destination AE not configured")
            return False
        try:
            self._assoc = self.destination_ae.associate(
                self.destination_host, self.destination_port, ae_title=self.destination_aet
            )
        except Exception as e:
            logger.error(f"Error opening forward association: {e}")
            self._assoc = None
            return False
        if not self._assoc.is_established:
            logger.error(
                f"Failed to establish association with "
                f"{self.destination_aet}@{self.destination_host}:{self.destination_port}"
            )
            self._assoc = None
            return False
        return True

    def _forward_instance(self, ds: Dataset) -> int:
        if not self._ensure_assoc():
            return 0xC000
        assert self._assoc is not None
        status = self._assoc.send_c_store(ds)
        if status and status.Status == 0x0000:
            logger.debug(f"Forwarded instance {ds.SOPInstanceUID} to {self.destination_aet}")
            return 0x0000
        logger.error(
            f"Failed to forward instance {ds.SOPInstanceUID}: "
            f"status={status.Status if status else 'None'}"
        )
        return 0xC000

    def close(self) -> None:
        """Release the held FORWARD association, if any."""
        if self._assoc is not None and self._assoc.is_established:
            self._assoc.release()
        self._assoc = None

    def get_stored_instances(self) -> dict[str, Dataset]:
        return self.stored_instances

    def clear_stored_instances(self) -> None:
        self.stored_instances.clear()


def create_store_handler(
    mode: StorageMode,
    output_dir: Path | None = None,
    destination_ae: ApplicationEntity | None = None,
    destination_aet: str | None = None,
    destination_host: str | None = None,
    destination_port: int | None = None,
) -> tuple[list[tuple[Any, Any]], StorageHandler]:
    handler = StorageHandler(
        mode=mode,
        output_dir=output_dir,
        destination_ae=destination_ae,
        destination_aet=destination_aet,
        destination_host=destination_host,
        destination_port=destination_port,
    )
    handlers = [(evt.EVT_C_STORE, handler.handle_store)]
    return handlers, handler
