"""Assessment providers beyond the stub. `provider_from_env()` is the single seam used by the app factory."""
from __future__ import annotations

import logging
import os

from ..provider import AssessmentProvider, StubProvider
from .bundle_provider import BundleError, BundleProvider, QualifiedProviderResult, ShadowProviderResult

log = logging.getLogger("garage.provider")

__all__ = ["BundleError", "BundleProvider", "QualifiedProviderResult", "ShadowProviderResult", "provider_from_env"]


def provider_from_env(environ=None, *, allow_synthetic: bool = False) -> AssessmentProvider:
    """`BundleProvider` when GARAGE_MODEL_BUNDLE names a bundle that verifies now; otherwise the stub.
    A bad bundle is logged and replaced by the (unavailable) stub: it never stops the app from starting.

    Qualified display (step 9) additionally needs, from the environment and never from inside the bundle:
      GARAGE_MODEL_BUNDLE_PIN              the receipt content sha256 recorded out-of-band by the owner (open decision O10)
      GARAGE_PERCENT_ROUNDING_READING      float_expression | decimal_repr | binary_exact, REQUIRED, no default (open decision O9)
    and a QUALIFIED_V2.json sidecar that verifies. Anything less leaves the bundle in shadow mode. `allow_synthetic` is a
    code argument for fixture tests only; no environment variable sets it."""
    env = os.environ if environ is None else environ
    path = (env.get("GARAGE_MODEL_BUNDLE") or "").strip()
    if not path:
        return StubProvider()
    try:
        provider = BundleProvider(path, pin=(env.get("GARAGE_MODEL_BUNDLE_PIN") or "").strip() or None,
                                  rounding_reading=(env.get("GARAGE_PERCENT_ROUNDING_READING") or "").strip() or None,
                                  allow_synthetic=allow_synthetic)
    except BundleError as e:
        log.error("GARAGE_MODEL_BUNDLE rejected (%s): using the stub provider", e.code)
        return StubProvider()
    except Exception:
        log.exception("GARAGE_MODEL_BUNDLE could not be loaded: using the stub provider")
        return StubProvider()
    if provider.qualified:
        log.warning("QUALIFIED model bundle loaded: model_version=%s", provider.model_version)
        return provider
    log.warning("shadow model bundle loaded: model_version=%s (unqualified; no estimate is shown)",
                provider.model_version)
    return provider
