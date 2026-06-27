# dimsechord

Neutral pure-Python DICOM + DICOMweb core shared by `dicarina` and `clarinet`.

## Public API

The supported surface is exactly what `dimsechord/__init__.py` exports
(`from dimsechord import …`); it is frozen by `tests/unit/test_public_api.py`.

Everything else lives in underscore-prefixed modules (`dimsechord._scu`,
`dimsechord._cache`, …) and is **private** — importing from those modules is
unsupported and may break without notice. If you need something that is not
exported, extend the public surface rather than reaching into a private module.
