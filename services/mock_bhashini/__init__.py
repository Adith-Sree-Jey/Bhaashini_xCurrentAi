"""Dev-only stand-in for the on-device Bhashini models service.

The real platform exposes ``bhashini_models.service`` as a systemd unit on
``http://localhost:11400`` with ``/asr``, ``/nmt``, ``/tts``, and ``/health``
routes (see CLAUDE.md). This package implements the same contract, backed
by deterministic fake ASR/NMT/TTS logic (see
:mod:`services.mock_bhashini.fake_backends`) instead of real models, so
:mod:`puriyudha.clients` can be exercised without the real service or any
network access.

Run it with ``python -m services.mock_bhashini`` (see
:mod:`services.mock_bhashini.server` for the CLI flags, including
``--latency`` and ``--failure-rate``).
"""
