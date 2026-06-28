"""Async DICOM client façade (find / store / move) over the sync SCU."""

# ruff: noqa: ASYNC109  # timeout is a DICOM socket timeout, not an asyncio cancel scope

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydicom import Dataset

from dimsechord._models import (
    AssociationConfig,
    BatchStoreResult,
    DicomNode,
    ImageQuery,
    ImageResult,
    QueryRetrieveLevel,
    RetrieveRequest,
    RetrieveResult,
    SeriesQuery,
    SeriesResult,
    StorageConfig,
    StorageMode,
    StudyQuery,
    StudyResult,
)
from dimsechord._scu import DicomOperations

logger = logging.getLogger(__name__)


class DicomClient:
    """Async façade exposing the sync SCU via ``asyncio.to_thread``."""

    def __init__(self, calling_aet: str, max_pdu: int = 16384) -> None:
        self.calling_aet = calling_aet
        self.max_pdu = max_pdu
        self._operations = DicomOperations(calling_aet=calling_aet, max_pdu=max_pdu)

    @classmethod
    def set_max_concurrent_associations(cls, n: int) -> None:
        """Set a process-global cap on concurrent DICOM associations.

        Applies to every association — find / get / move / store — including
        those opened by ``PullEngine`` (the cap is class-level on the shared
        sync SCU). Distinct from ``AssociationPool``, which gates only C-MOVE
        AET leases.

        Raises ``ValueError`` if ``n`` < 1; ``0`` would install a zero-count
        semaphore that blocks every association forever.
        """
        if n < 1:
            raise ValueError("n must be >= 1")
        DicomOperations.set_association_semaphore(n)

    def _create_association_config(
        self, called_aet: str, peer_host: str, peer_port: int, timeout: float = 30.0
    ) -> AssociationConfig:
        return AssociationConfig(
            calling_aet=self.calling_aet,
            called_aet=called_aet,
            peer_host=peer_host,
            peer_port=peer_port,
            max_pdu=self.max_pdu,
            timeout=timeout,
        )

    async def find_studies(
        self, query: StudyQuery, peer: DicomNode, timeout: float = 30.0
    ) -> list[StudyResult]:
        config = self._create_association_config(peer.aet, peer.host, peer.port, timeout)
        return await asyncio.to_thread(self._operations.find_studies, config, query)

    async def find_series(
        self, query: SeriesQuery, peer: DicomNode, timeout: float = 30.0
    ) -> list[SeriesResult]:
        config = self._create_association_config(peer.aet, peer.host, peer.port, timeout)
        return await asyncio.to_thread(self._operations.find_series, config, query)

    async def find_images(
        self, query: ImageQuery, peer: DicomNode, timeout: float = 30.0
    ) -> list[ImageResult]:
        config = self._create_association_config(peer.aet, peer.host, peer.port, timeout)
        return await asyncio.to_thread(self._operations.find_images, config, query)

    async def store_instance(
        self, dataset: Dataset, peer: DicomNode, timeout: float = 30.0
    ) -> bool:
        config = self._create_association_config(peer.aet, peer.host, peer.port, timeout)
        return await asyncio.to_thread(self._operations.store_instance, config, dataset)

    async def store_instances_batch(
        self, datasets: list[Dataset], peer: DicomNode, timeout: float = 300.0
    ) -> BatchStoreResult:
        config = self._create_association_config(peer.aet, peer.host, peer.port, timeout)
        return await asyncio.to_thread(self._operations.store_instances_batch, config, datasets)

    async def move_study(
        self,
        study_uid: str,
        peer: DicomNode,
        destination_aet: str,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        config = self._create_association_config(peer.aet, peer.host, peer.port, timeout)
        request = RetrieveRequest(
            level=QueryRetrieveLevel.STUDY, study_instance_uid=study_uid, patient_id=patient_id
        )
        return await asyncio.to_thread(
            self._operations.move_study, config, request, destination_aet
        )

    async def move_series(
        self,
        study_uid: str,
        series_uid: str,
        peer: DicomNode,
        destination_aet: str,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        config = self._create_association_config(peer.aet, peer.host, peer.port, timeout)
        request = RetrieveRequest(
            level=QueryRetrieveLevel.SERIES,
            study_instance_uid=study_uid,
            series_instance_uid=series_uid,
            patient_id=patient_id,
        )
        return await asyncio.to_thread(
            self._operations.move_study, config, request, destination_aet
        )

    async def _retrieve_via_get(
        self,
        *,
        level: QueryRetrieveLevel,
        mode: StorageMode,
        study_uid: str,
        series_uid: str | None,
        peer: DicomNode,
        output_dir: Path | None,
        patient_id: str | None,
        timeout: float,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> RetrieveResult:
        config = self._create_association_config(peer.aet, peer.host, peer.port, timeout)
        request = RetrieveRequest(
            level=level,
            study_instance_uid=study_uid,
            series_instance_uid=series_uid,
            patient_id=patient_id,
        )
        storage = StorageConfig(mode=mode, output_dir=output_dir)
        return await asyncio.to_thread(
            self._operations.retrieve_via_get, config, request, storage, on_progress
        )

    async def get_study(
        self,
        study_uid: str,
        peer: DicomNode,
        output_dir: Path,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        return await self._retrieve_via_get(
            level=QueryRetrieveLevel.STUDY,
            mode=StorageMode.DISK,
            study_uid=study_uid,
            series_uid=None,
            peer=peer,
            output_dir=output_dir,
            patient_id=patient_id,
            timeout=timeout,
        )

    async def get_series(
        self,
        study_uid: str,
        series_uid: str,
        peer: DicomNode,
        output_dir: Path,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        return await self._retrieve_via_get(
            level=QueryRetrieveLevel.SERIES,
            mode=StorageMode.DISK,
            study_uid=study_uid,
            series_uid=series_uid,
            peer=peer,
            output_dir=output_dir,
            patient_id=patient_id,
            timeout=timeout,
        )

    async def get_study_to_memory(
        self,
        study_uid: str,
        peer: DicomNode,
        patient_id: str | None = None,
        timeout: float = 300.0,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> RetrieveResult:
        return await self._retrieve_via_get(
            level=QueryRetrieveLevel.STUDY,
            mode=StorageMode.MEMORY,
            study_uid=study_uid,
            series_uid=None,
            peer=peer,
            output_dir=None,
            patient_id=patient_id,
            timeout=timeout,
            on_progress=on_progress,
        )

    async def get_series_to_memory(
        self,
        study_uid: str,
        series_uid: str,
        peer: DicomNode,
        patient_id: str | None = None,
        timeout: float = 300.0,
    ) -> RetrieveResult:
        return await self._retrieve_via_get(
            level=QueryRetrieveLevel.SERIES,
            mode=StorageMode.MEMORY,
            study_uid=study_uid,
            series_uid=series_uid,
            peer=peer,
            output_dir=None,
            patient_id=patient_id,
            timeout=timeout,
        )
