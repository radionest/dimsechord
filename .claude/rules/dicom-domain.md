---
paths: "src/dimsechord/**"
---

# DICOM / DIMSE domain vocabulary

dimsechord implements DIMSE networking (the DICOM wire protocol), not just
DICOM file parsing. This file is a glossary — for the deeper "why", see
`docs/why.md`.

## Core terms

- **AE (Application Entity)** — a named DICOM network endpoint (identified
  by an AE Title). Every SCU/SCP role in this package acts as one.
- **Association** — a negotiated network connection between two AEs; DIMSE
  operations only happen inside an open association.
- **SCU (Service Class User)** — the party that *requests* an operation
  (e.g. `DicomClient` querying a PACS).
- **SCP (Service Class Provider)** — the party that *serves* a request
  (e.g. `StorageSCP` receiving instances).

## DIMSE operations used in this package

- **C-FIND** — query for studies/series/images matching criteria.
- **C-MOVE** — ask a remote SCP to *push* matching instances to a named
  destination AE via a new association. It does not return instances to
  the requester directly.
- **C-STORE** — send/receive actual DICOM instances.
- **C-GET** — retrieve instances over the *same* association that
  requested them (alternative to C-MOVE that avoids running an SCP).

## Query/Retrieve levels

`QueryRetrieveLevel` (`_models.py`) is one of `STUDY`, `SERIES`, `IMAGE` —
the granularity a C-FIND/C-MOVE operates at, each with a dedicated query
dataclass (`StudyQuery`/`SeriesQuery`/`ImageQuery`). DICOM also defines a
PATIENT level; the typed face deliberately has none.

## Information model

The typed face (`DicomClient`, the typed `DicomOperations` operations,
`PullEngine`) speaks the **Study Root** Q/R information model exclusively,
because its query types already encode that hierarchy: `study_instance_uid`
is required at series/image level and patient attributes are study-level
matching keys, so every identifier is hierarchically complete by
construction. No Patient Root context is requested. Anything else —
Patient Root, relational
queries, peer-specific identifier shapes — is the raw pass-through face's
job (`find_iter` / `QueryEngine`), where the caller owns the identifier
and the model.

## The "move-to-self" pattern

Because C-MOVE pushes instead of returning instances, retrieving to
yourself means running a Storage SCP, getting the PACS to route the
destination AET back to that SCP, and coordinating the move with the
receive queue. `PullEngine` does all of this: it issues a C-MOVE
targeting dimsechord's own AE, then receives the pushed instances through
its own persistent `StorageSCP` (`_scp.py`), streaming them out one by
one. On a cache miss it delegates to one of two transport strategies (see
`_pull_engine.py`):

- `_MoveToSelfTransport` — the C-MOVE-to-self flow described above
- `_CGetTransport` — a direct C-GET flow, for PACS that don't support or
  need move-to-self (public entry points: `PullEngine.via_cget`,
  `DicomClient.get_*`)

See `docs/why.md` ("Move-to-self retrieval") for the full rationale.
