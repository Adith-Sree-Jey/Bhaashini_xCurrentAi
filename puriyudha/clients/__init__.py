"""Narrow client interfaces to external services: BhashiniClient, OllamaClient,
and ASR/TTS adapters.

Every external dependency gets a narrow interface here plus a
recorded-fixture fake used in tests, so the rest of the codebase is
importable and testable without a Jetson, without Ollama running, and
without network (CLAUDE.md engineering rules).
"""
from puriyudha.clients.bhashini import BhashiniClient
from puriyudha.clients.deadline import DEFAULT_SESSION_BUDGET_SECONDS, Deadline
from puriyudha.clients.ollama import OllamaClient

__all__ = [
    "BhashiniClient",
    "OllamaClient",
    "Deadline",
    "DEFAULT_SESSION_BUDGET_SECONDS",
]
