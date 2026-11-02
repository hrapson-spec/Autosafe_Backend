"""Assessment providers beyond the stub. `provider_from_env()` is the single seam used by the app factory."""
from __future__ import annotations

import logging
import os

from ..provider import AssessmentProvider, StubProvider
from .bundle_provider import BundleError, BundleProvider, ShadowProviderResult

log = logging.getLogger("garage.provider")

__all__ = ["BundleError", "BundleProvider", "ShadowProviderResult", "provider_from_env"]


def provider_from_env(environ=None) -> AssessmentProvider:
    """`BundleProvider` when GARAGE_MODEL_BUNDLE names a bundle that verifies now; otherwise the stub.
    A bad bundle is logged and replaced by the (unavailable) stub: it never stops the app from starting."""
    env = os.environ if environ is None else environ
    path = (env.get("GARAGE_MODEL_BUNDLE") or "").strip()
    if not path:
        return StubProvider()
    try:
        provider = BundleProvider(path)
    except BundleError as e:
        log.error("GARAGE_MODEL_BUNDLE rejected (%s): using the stub provider", e.code)
        return StubProvider()
    except Exception:
        log.exception("GARAGE_MODEL_BUNDLE could not be loaded: using the stub provider")
        return StubProvider()
    log.warning("shadow model bundle loaded: model_version=%s (unqualified; no estimate is shown)",
                provider.model_version)
    return provider
