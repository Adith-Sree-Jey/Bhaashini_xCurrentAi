# CLAUDE.md — Puriyudha? project context

Read this file fully before any task. It encodes hard constraints discovered from the
upstream platform. Violating them costs days later.

## What this project is

An **offline medication-comprehension appliance** for Indian pharmacy and hospital
discharge counters. It reads a printed prescription or blister strip via camera, listens to
the pharmacist, converts both into a **validated structured medication record**, explains
that record to the patient in their language as plain speech plus pictograms, then **asks
the patient to say it back, scores understanding slot by slot, and re-explains only the
slots they got wrong.**

Target hardware: NVIDIA Jetson Orin Nano 8GB Super Dev Kit running the Suno Sutra platform.
**We do not have the hardware until 5 Aug 2026.** Everything must be built and tested on a
laptop before then, behind interfaces that make the on-device swap zero-code.

## Non-negotiable product invariants

1. **No network calls at inference time. Ever.** Not for models, not for fonts, not for
   telemetry. If a module needs the network, it needs it at build time only, and that must
   be explicit. Any inference-path import of `requests` to a non-localhost address is a bug.
2. **The language model is never the source of a medical fact.** Drug identity, strength,
   dose range and form come from the offline formulary. The schedule comes from the rules
   parser first. The LLM only fills gaps the rules parser could not, and its output is
   always validated against the formulary before it is emitted.
3. **Refuse rather than guess.** Below the confidence threshold the system emits a
   `NeedsHumanConfirmation` result naming the top-3 candidates. It never speaks a drug name
   it is not confident about. Refusal rate is a reported metric, not a failure.
   **Enforced by the type system, not a convention:** `puriyudha.schema.validate()` is the
   only place a `ConfirmedOrder` can be constructed (private constructor token; see that
   class's docstring), and every patient-facing function -- anything in `render/`, `issue/`,
   or anything that produces speech, a screen frame, or a QR payload -- takes a
   `ConfirmedOrder` in its signature, never a bare `MedicationOrder`. A low-confidence or
   never-validated order is structurally unable to reach a patient (verification-pass
   finding H3). Patient-facing functions must also call
   `puriyudha.schema.require_confirmed_order()` as their first line: a type hint alone is
   not enforced by Python at runtime.
4. **No PHI is persisted.** Patient names, hospital IDs and addresses are never stored,
   logged, or included in any artifact. If OCR captures them, they are dropped before the
   record is constructed.
5. **Deterministic before probabilistic.** Any behaviour that can be a rule is a rule.
6. **The system MUST produce a complete, correct MedicationOrder for supported inputs
   using deterministic rules alone.** Language-model assistance is an optional enhancement
   that can be disabled entirely by configuration, and the system's core claims must hold
   with it disabled. (Added when the LLM fallback extractor, T4, was cut from the critical
   path due to schedule slip -- a designed property now, not a temporary descope.) The gate
   is `puriyudha.config.PipelineConfig.llm_enabled`, defaulting to `False`; when `False`, no
   Ollama call is made anywhere in the pipeline and `OllamaClient` is not even constructed
   -- see `puriyudha.extract.get_ollama_client`'s deferred-import mechanism. `OllamaClient`
   itself is not deleted: it stays implemented and tested, ready to be re-enabled by
   configuration. The evaluation harness (T7) reports the same pipeline run with
   `llm_enabled` `True` and `False` and computes a slot-level F1 delta
   (`puriyudha.runtime.metrics`) -- this measured delta is a headline result.

## Upstream platform facts (verified — do not re-derive, do not assume otherwise)

- Repos: `currentai-org/suno-sutra-sw` and `suno-sutra-hw`, MIT licensed.
- Framework package: **`pocketinfer`**. Applications live in
  `python/pocketinfer/applications/`, subclass `BaseApplication`, and are registered with
  the `@RegisterApplication({...})` decorator carrying a metadata dict with `models` and
  `service_dependencies` keys.
- Run an app: `pocketinfer-service --app <ClassName>`. Off-device development is supported
  via `pocketinfer-service --dummy-board --audio-file x.wav --image-file y.jpg`.
- **Board HAL methods we may use:** `board.audio.start()`, `board.audio.stop()`,
  `board.audio.to_audio_data()`, `board.camera_frame_jpg()`, `board.camera_frame()`,
  `board.wait_for_trigger_button_down()`, `board.wait_for_trigger_button_up()`,
  `board.clear_screen()`, `board.statusbar(text)`, `board.top_text(text)`,
  `board.bottom_text(text)`, `board.mode_text(text)`, `board.led_animation(v)`,
  `board.subscribe_to_ui(cb)`, `board.alsa_playback_device`.
- **Bhashini models are NOT a Python library.** They are a systemd unit
  (`bhashini_models.service`) exposing HTTP on **`http://localhost:11400`**:
  - `POST /asr`  → `{"language": str, "audio_base64": str}`
  - `POST /nmt`  → `{"text": str, "src_lang": str, "tgt_lang": str}` → `{"translated_text": ...}`
  - `POST /tts`  → `{"text": str, "language": str}` → `{"audio_base64": ...}` (WAV)
  - `GET  /health`
  NMT runs on CTranslate2. TTS is Flite with Indic voices. Language codes in the reference
  app are lowercase two-letter for ASR/TTS and uppercase `"EN"` for NMT targets — treat the
  exact casing as unverified and normalise at our boundary.
- **LLM runtime is Ollama**, not llama.cpp. Available models: `qwen3-vl:2b` (vision),
  `ministral-3:3B` (text). `moondream:1.8B` is referenced as an alternative VLM.
- English-only fallbacks in the platform: **Vosk** ASR (`vosk-model-small-en-us-0.15`) and
  **Piper** TTS (`en_US-lessac-medium`).
- Display: **ILI9341, 320×240, rotation 90**, over SPI, with an XPT2046 resistive touch
  controller. Trigger button is a TTP223 on header pin 7 (`GP167`).
- **The upstream UI ships only `NotoSansDevanagari` `.pcf` fonts.** Tamil support does not
  exist and we must add it.
- Known upstream bugs (do not replicate, and we will PR fixes): `models/nmt.py` and
  `models/tts.py` call `check_output` and `time` without importing them;
  `hear_the_world.py` lists `"bashini_models"` (typo) in `service_dependencies`.

## Repository layout we are building

```
puriyudha/
  puriyudha/
    schema.py          # MedicationOrder, PatientUnderstanding, dataclasses + validation
    formulary/         # SQLite build + fuzzy/phonetic matcher
    sig/               # rules-based prescription shorthand parser
    extract/           # LLM fallback extractor + merge policy
    vision/            # image preprocess + OCR + layout classification
    render/            # language templates, pictogram renderer, 320x240 layout
    verify/            # slot comparator + repair loop
    issue/             # QR payload + audio artifact
    runtime/           # model scheduler, timing + power instrumentation
    clients/           # BhashiniClient, OllamaClient, ASR/TTS adapters
    app.py             # the pocketinfer application (thin; all logic in modules above)
  services/
    mock_bhashini/     # dev-only stand-in for the :11400 service
  data/                # generated SQLite, fonts, pictograms, eval fixtures
  tests/
  tools/               # CLI utilities: label, evaluate, report
```

## Engineering rules

- Python 3.10 (matches JetPack 6.2's `cp310`). No 3.11+ syntax.
- Every module is importable and testable **without** a Jetson, without Ollama running, and
  without network. Achieve this with a narrow client interface per external dependency plus
  a recorded-fixture fake used in tests.
- `pytest`. Every task ships tests. Tests must be hermetic — **no test may call Ollama, the
  real Bhashini service, or the network.** Use fixtures under `tests/fixtures/`.
- Type-hint public functions. `dataclasses` for records. No heavyweight frameworks.
- Keep dependencies minimal and aarch64-installable. Prefer `opencv-python`, `numpy`,
  `rapidfuzz`, `pillow`, `qrcode`, `pytesseract` — all already viable on Jetson. **Do not**
  add torch, transformers, or anything that pulls a second CUDA stack.
- Log timing for every pipeline stage in the same JSONL shape the reference app uses, so our
  numbers are comparable to the baseline.
- No emoji in code, output, or UI text.

## Language scope

Core: **Tamil (ta)** and **Hindi (hi)** output, with English (en) as the clinician-side
input language. Everything else is a stretch goal. All user-facing strings live in a
per-language resource file; never inline a translated string in code.

## What "done" means for any task

1. It runs from a single documented command.
2. `pytest` passes with no network access.
3. There is a `--demo` or CLI entry point a human can eyeball in under a minute.
4. The README section for that module is updated with the command and expected output.
