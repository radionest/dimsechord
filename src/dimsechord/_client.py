"""Async DICOM client façade (find / store / move) over the sync SCU."""

# ruff: noqa: ASYNC109  # timeout is a DICOM socket timeout, not an asyncio cancel scope

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
