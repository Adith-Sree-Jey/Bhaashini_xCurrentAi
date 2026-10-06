"""LLM fallback extractor and merge policy.

Only fills gaps the rules parser (:mod:`puriyudha.sig`) could not, and its
output is always validated against the formulary before being emitted. Not
yet implemented as an extractor (T4).

**Architecture change (verification-pass finding, part 5):** the LLM
fallback extractor is cut from the critical path. CLAUDE.md: "The system
MUST produce a complete, correct MedicationOrder for supported inputs using
deterministic rules alone. Language-model assistance is an optional
enhancement that can be disabled entirely by configuration, and the
system's core claims must hold with it disabled." :data:`get_ollama_client`
below is the gate that makes this true structurally, not just by
convention: it is the only place in this module that may import
:mod:`puriyudha.clients.ollama`, and it imports it lazily, inside the
``if config.llm_enabled`` branch -- so ``import puriyudha.extract`` alone,
and any call to this function with ``llm_enabled=False`` (the default,
see :mod:`puriyudha.config`), never pulls the Ollama client into
``sys.modules`` at all, and therefore never constructs an ``OllamaClient``
or makes an Ollama call. The client itself is not deleted (part 5.2): it
stays fully implemented and tested, ready to be re-enabled by
configuration when there is time to build a real LLM-assisted extraction
path on top of it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from puriyudha.config import PipelineConfig

if TYPE_CHECKING:  # only for type-checkers -- never executed, never imports at runtime
    from puriyudha.clients.ollama import OllamaClient


def get_ollama_client(config: PipelineConfig, **client_kwargs) -> "Optional[OllamaClient]":
    """Returns a constructed :class:`~puriyudha.clients.ollama.OllamaClient`
    if ``config.llm_enabled``, else ``None`` -- and, critically, never even
    *imports* :mod:`puriyudha.clients.ollama` when disabled. Every other
    place in this module that eventually needs an Ollama call must go
    through this function rather than importing OllamaClient directly, so
    the ``llm_enabled`` gate cannot be bypassed by accident.

    ``client_kwargs`` are forwarded to ``OllamaClient(...)`` unchanged
    (e.g. ``base_url=``, ``session=`` for tests) when enabled.
    """
    if not config.llm_enabled:
        return None
    from puriyudha.clients.ollama import OllamaClient  # deferred -- see module docstring

    return OllamaClient(**client_kwargs)
