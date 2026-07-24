"""Presentation-context defaults and builder for storage SCU negotiation.

Private module — import these names from ``dimsechord`` instead.

pynetdicom facts that shape this module (verified against 3.0.4):

- For UNcompressed datasets pynetdicom may convert Implicit/Explicit VR and
  endianness, so ONE context with ``DEFAULT_TRANSFER_SYNTAXES`` covers all of
  them.
- For a COMPRESSED dataset an exactly matching accepted context is required
  (no conversion), and an SCP accepts a single transfer syntax per context —
  so every (SOP class, compressed TS) pair needs its own requested context.
- An association carries at most 128 presentation contexts, so "every storage
  class x every syntax" cannot fit; the defaults below curate the image
  classes that realistically travel compressed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydicom.uid import UID
from pynetdicom.presentation import DEFAULT_TRANSFER_SYNTAXES, build_context
from pynetdicom.sop_class import (  # type: ignore[attr-defined]
    BasicTextSRStorage,
    ColorSoftcopyPresentationStateStorage,
    Comprehensive3DSRStorage,
    ComprehensiveSRStorage,
    ComputedRadiographyImageStorage,
    CTImageStorage,
    DigitalXRayImageStorageForPresentation,
    EncapsulatedCDAStorage,
    EncapsulatedPDFStorage,
    EnhancedCTImageStorage,
    EnhancedMRImageStorage,
    EnhancedSRStorage,
    GrayscaleSoftcopyPresentationStateStorage,
    KeyObjectSelectionDocumentStorage,
    MRImageStorage,
    PositronEmissionTomographyImageStorage,
    RawDataStorage,
    RTDoseStorage,
    RTImageStorage,
    RTPlanStorage,
    RTStructureSetStorage,
    SecondaryCaptureImageStorage,
    SegmentationStorage,
    SurfaceSegmentationStorage,
    UltrasoundImageStorage,
    UltrasoundMultiFrameImageStorage,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pynetdicom.presentation import PresentationContext

#: Compressed transfer syntaxes negotiated by default — the ones that
#: realistically appear on clinical PACS traffic.
DEFAULT_COMPRESSED_TRANSFER_SYNTAXES: tuple[str, ...] = (
    "1.2.840.10008.1.2.4.50",  # JPEG Baseline (Process 1)
    "1.2.840.10008.1.2.4.51",  # JPEG Extended (Process 2 & 4)
    "1.2.840.10008.1.2.4.70",  # JPEG Lossless SV1 (Process 14)
    "1.2.840.10008.1.2.4.80",  # JPEG-LS Lossless
    "1.2.840.10008.1.2.4.81",  # JPEG-LS Near-Lossless
    "1.2.840.10008.1.2.4.90",  # JPEG 2000 Lossless Only
    "1.2.840.10008.1.2.4.91",  # JPEG 2000
    "1.2.840.10008.1.2.5",  # RLE Lossless
)

#: Image SOP classes that realistically travel compressed. Each costs
#: ``1 + len(compressed_syntaxes)`` contexts, so the list is curated.
DEFAULT_IMAGE_STORAGE_CLASSES: tuple[str, ...] = (
    str(CTImageStorage),
    str(EnhancedCTImageStorage),
    str(MRImageStorage),
    str(EnhancedMRImageStorage),
    str(UltrasoundImageStorage),
    str(UltrasoundMultiFrameImageStorage),
    str(SecondaryCaptureImageStorage),
    str(ComputedRadiographyImageStorage),
    str(DigitalXRayImageStorageForPresentation),
    str(PositronEmissionTomographyImageStorage),
)

#: Non-image SOP classes forwarded with a single uncompressed context each.
DEFAULT_OTHER_STORAGE_CLASSES: tuple[str, ...] = (
    str(BasicTextSRStorage),
    str(EnhancedSRStorage),
    str(ComprehensiveSRStorage),
    str(Comprehensive3DSRStorage),
    str(GrayscaleSoftcopyPresentationStateStorage),
    str(ColorSoftcopyPresentationStateStorage),
    str(KeyObjectSelectionDocumentStorage),
    str(EncapsulatedPDFStorage),
    str(EncapsulatedCDAStorage),
    str(RTDoseStorage),
    str(RTStructureSetStorage),
    str(RTPlanStorage),
    str(RTImageStorage),
    str(SegmentationStorage),
    str(SurfaceSegmentationStorage),
    str(RawDataStorage),
)


def build_storage_scu_contexts(
    image_classes: Sequence[str] = DEFAULT_IMAGE_STORAGE_CLASSES,
    compressed_syntaxes: Sequence[str] = DEFAULT_COMPRESSED_TRANSFER_SYNTAXES,
    other_classes: Sequence[str] = DEFAULT_OTHER_STORAGE_CLASSES,
    max_contexts: int = 128,
) -> list[PresentationContext]:
    """Build requested presentation contexts for a storage SCU.

    Per image class: one context with pynetdicom's ``DEFAULT_TRANSFER_SYNTAXES``
    (covers everything uncompressed, Deflated included) plus one single-syntax
    context per compressed transfer syntax. Per other class: one uncompressed
    context. The defaults produce 106 contexts, leaving headroom below the
    DICOM limit of 128 for caller additions.

    Intended use for a forwarding SCU::

        ae.requested_contexts = build_storage_scu_contexts()

    Args:
        image_classes: SOP class UIDs that need compressed syntaxes.
        compressed_syntaxes: Compressed transfer syntax UIDs to negotiate
            for every image class.
        other_classes: SOP class UIDs negotiated uncompressed only.
        max_contexts: Context budget; lower it when the association must
            carry additional non-storage contexts (e.g. C-GET reserves 2).

    Raises:
        ValueError: If the resulting context count exceeds ``max_contexts``.
    """
    total = len(image_classes) * (1 + len(compressed_syntaxes)) + len(other_classes)
    if total > max_contexts:
        raise ValueError(
            f"{total} presentation contexts exceed the limit of {max_contexts}: "
            f"{len(image_classes)} image classes x (1 + {len(compressed_syntaxes)} "
            f"compressed syntaxes) + {len(other_classes)} other classes"
        )
    contexts: list[PresentationContext] = []
    for cls in image_classes:
        contexts.append(build_context(cls, DEFAULT_TRANSFER_SYNTAXES))
        for ts in compressed_syntaxes:
            contexts.append(build_context(cls, [ts]))
    for cls in other_classes:
        contexts.append(build_context(cls, DEFAULT_TRANSFER_SYNTAXES))
    return contexts


def build_storage_scp_contexts(
    image_classes: Sequence[str] = DEFAULT_IMAGE_STORAGE_CLASSES,
    compressed_syntaxes: Sequence[str] = DEFAULT_COMPRESSED_TRANSFER_SYNTAXES,
    other_classes: Sequence[str] = DEFAULT_OTHER_STORAGE_CLASSES,
) -> list[PresentationContext]:
    """Supported (acceptor-side) storage contexts from the shared matrix.

    Mirror of ``build_storage_scu_contexts``: for identical arguments, every
    (SOP class, transfer syntax) pair these contexts accept is proposable by
    the SCU set — an instance a relay SCP accepts is always negotiable
    upstream. Supported contexts are a matching table, never proposed, so
    there is no 128-context limit here. Intended use::

        for cx in build_storage_scp_contexts():
            ae.add_supported_context(cx.abstract_syntax, cx.transfer_syntax)
    """
    contexts: list[PresentationContext] = []
    for cls in image_classes:
        contexts.append(
            build_context(cls, [*DEFAULT_TRANSFER_SYNTAXES, *compressed_syntaxes])
        )
    for cls in other_classes:
        contexts.append(build_context(cls, DEFAULT_TRANSFER_SYNTAXES))
    return contexts


def matches_accepted_context(
    contexts: Sequence[PresentationContext],
    sop_class_uid: str,
    transfer_syntax: str,
) -> bool:
    """True if a C-STORE of (SOP class, transfer syntax) fits an accepted context.

    Mirrors pynetdicom's SCU context matching (3.0.4, association.py:473-497):
    exact transfer-syntax match; otherwise conversion is possible only between
    uncompressed syntaxes of the same endianness (explicit<->implicit,
    deflated<->inflated); compressed syntaxes never convert.
    """
    ts = UID(transfer_syntax)
    for cx in contexts:
        if cx.abstract_syntax != sop_class_uid:
            continue
        cx_ts = UID(cx.transfer_syntax[0])
        if cx_ts == ts:
            return True
        if ts.is_compressed or cx_ts.is_compressed:
            continue
        if ts.is_little_endian != cx_ts.is_little_endian:
            continue
        return True
    return False
