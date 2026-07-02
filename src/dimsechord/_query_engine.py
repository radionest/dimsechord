"""QueryEngine: pool-gated raw C-FIND pass-through.

Symmetric to PullEngine (pool-gated retrieval): QueryEngine leases a find
identity per query and streams raw pending-response identifiers — sync for
the DIMSE SCP thread, async (via the shared bounded bridge) for the HTTP
face. No typed parsing: the identifier goes out unmodified and responses
come back as the peer sent them.
"""

# ruff: noqa: ASYNC109  # timeout is a DICOM socket timeout, not an asyncio cancel scope

from __future__ import annotations

from typing import TYPE_CHECKING

from dimsechord._bridge import iter_to_aiter
from dimsechord._models import AssociationConfig
from dimsechord._scu import DicomOperations

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from pydicom import Dataset

    from dimsechord._models import DicomNode
    from dimsechord._pool import AssociationPool


class QueryEngine:
    """Streams raw C-FIND responses through a leased pool identity."""

    def __init__(
        self,
        pool: AssociationPool,
        pacs: DicomNode,
        *,
        max_pdu: int = 16384,
        find_timeout: float = 30.0,
    ) -> None:
        self._pool = pool
        self._pacs = pacs
        self._max_pdu = max_pdu
        self._find_timeout = find_timeout

    def iter_find(
        self, identifier: Dataset, *, model: str, timeout: float | None = None
    ) -> Iterator[Dataset]:
        """Yield raw pending-response identifiers as they arrive.

        Leases a find identity for the lifetime of the generator; the lease is
        released whether the stream completes, fails, or is closed early
        (early close also aborts the upstream association — see
        ``DicomOperations.find_iter``).
        """
        t = self._find_timeout if timeout is None else timeout
        with self._pool.lease_find(timeout=t) as aet:
            # SCU built per lease so its AE title == the leased identity
            # (same reasoning as PullEngine's move transport).
            ops = DicomOperations(calling_aet=aet, max_pdu=self._max_pdu)
            config = AssociationConfig(
                calling_aet=aet,
                called_aet=self._pacs.aet,
                peer_host=self._pacs.host,
                peer_port=self._pacs.port,
                max_pdu=self._max_pdu,
                timeout=t,
            )
            yield from ops.find_iter(config, identifier, model)

    async def stream_find(
        self, identifier: Dataset, *, model: str, timeout: float | None = None
    ) -> AsyncIterator[Dataset]:
        async for ds in iter_to_aiter(
            lambda: self.iter_find(identifier, model=model, timeout=timeout)
        ):
            yield ds
