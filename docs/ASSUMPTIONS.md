# Assumptions that can only be settled on the real device

We do not have the Jetson Orin Nano until **5 Aug 2026** (CLAUDE.md). Every
row below is a claim this codebase currently makes -- in a comment, a
constant, a mock, or a piece of documentation -- that we could not verify
against real hardware and must settle the moment the device exists. Each
row names the exact command to run.

## Vendored platform pin

`vendor/suno-sutra-sw` is a git submodule pinned at commit
`89487b718da9eddbc302d93e2a04e79522c67bc3` on `main` (verified via
`git submodule status`, which reports it with a leading space -- i.e. the
checked-out commit exactly matches the pin, no drift). `.gitmodules` points
at the public GitHub URL (`https://github.com/currentai-org/suno-sutra-sw.git`),
not a local path, so a fresh clone by anyone else works.

## Unverified claims

| # | Claim (current assumption) | Where it's encoded | Command to settle on 6 Aug |
|---|---|---|---|
| 1 | `bhashini_models.service` exposes exactly `/asr`, `/nmt`, `/tts`, `/health` on port 11400, with the request/response shapes CLAUDE.md documents and `services/mock_bhashini` implements. | `puriyudha/clients/bhashini.py`, `tests/fixtures/contract/` | `python -m tools.verify_contract --base-url http://<device>:11400` |
| 2 | ASR/TTS `language` is always lowercase (`"en"`, `"hi"`, `"ta"`); NMT `src_lang`/`tgt_lang` is uppercase `"EN"` for English specifically, lowercase for `hi`/`ta`. Derived from the one reference caller in the vendored platform (`hear_the_world.py`), never confirmed against the real service. | `puriyudha/clients/bhashini.py::_LANGUAGE_CODES` | `python -m tools.capture_bhashini_contract --base-url http://<device>:11400 --record`, then `python -m tools.verify_contract --base-url http://<device>:11400` (flags every `language`/`src_lang`/`tgt_lang` field in any diff) |
| 3 | `/asr` response shape is `{"text": str}` -- not documented in CLAUDE.md at all, inferred from the one field the reference app reads (`asr_result['text']` in `pocketinfer/models/asr.py`). | `services/mock_bhashini/server.py` module docstring | Same as #1 -- `tools.verify_contract` diffs response field names automatically |
| 4 | Real Bhashini ASR emits Hindi/Tamil in native script (Devanagari/Tamil script), or as a Latin transliteration, or possibly both depending on config -- genuinely unknown; `services/mock_bhashini/fake_backends.py`'s messy-phrase fixtures include both forms specifically because we don't know which. | `services/mock_bhashini/fake_backends.py::_MESSY_PHRASES` module comment | `python -m tools.capture_bhashini_contract --base-url http://<device>:11400 --record`, then inspect the captured `hi`/`ta` `/asr` response `text` fields by eye |
| 5 | TTS WAV sample rate -- our mock synthesizes at 16kHz; CLAUDE.md says the real TTS backend is "Flite with Indic voices" but does not state its output sample rate. | `services/mock_bhashini/fake_backends.py::_TTS_SAMPLE_RATE` | After a real `/tts` capture: `python -c "import wave,sys; wf=wave.open(sys.argv[1]); print(wf.getframerate())" <(base64 -d captured_audio.b64)`, or inspect the recorded fixture's `audio_base64` the same way |
| 6 | The Jetson's pre-flashed SD image (if one ships) actually contains `suno-sutra-sw` checked out at (or compatible with) our pinned commit `89487b718da9eddbc302d93e2a04e79522c67bc3`. | `docs/dev_environment.md`, this file | On the device: `git -C /path/to/suno-sutra-sw rev-parse HEAD`, compare to the pin above |
| 7 | Whether the shipped image is pre-provisioned (i.e. `bhashini_models.service` and `ollama` already installed, enabled, and running) or needs first-boot setup. | `docs/STATUS.md` (blocked-on list) | On the device: `systemctl is-active bhashini_models.service ollama`; `curl http://localhost:11400/health`; `curl http://localhost:11434/api/tags` |
| 8 | Our `numpy`/`opencv-python` versions (unpinned in `constraints/x86-dev-requirements.txt` -- whatever `pip` resolves on x86_64) install and behave the same as whatever JetPack 6.2 / the device's `pip` resolves on aarch64. | `constraints/x86-dev-requirements.txt` | On the device, after `scripts/setup_dev.sh`: `.venv/bin/python -m pip freeze \| grep -iE "numpy\|opencv"`, compare against this laptop's `.venv/Scripts/python.exe -m pip freeze` (or equivalent) output |
| 9 | `pyaudio`, `vosk`, and `piper-tts` (the English-only fallback stack, `constraints/x86-dev-requirements.txt`) install cleanly via prebuilt wheels on aarch64/JetPack the way they did on this x86_64 Windows laptop (via a prebuilt `pyaudio` wheel) -- Linux ARM wheel availability for `pyaudio` specifically is the known risk `docs/dev_environment.md`'s `apt-get` step (`portaudio19-dev`) anticipates. | `scripts/setup_dev.sh`, `constraints/x86-dev-requirements.txt` | `bash scripts/setup_dev.sh` on the device; watch specifically for a `pyaudio` build failure |
| 10 | Available Ollama models on the device match CLAUDE.md's list exactly: `qwen3-vl:2b` (vision), `ministral-3:3B` (text) -- names, not just families/sizes; `puriyudha/clients/ollama.py`'s `DEFAULT_TEXT_MODEL`/`DEFAULT_VISION_MODEL` are hardcoded to these exact strings. | `puriyudha/clients/ollama.py` | On the device: `curl http://localhost:11434/api/tags`, confirm the exact model name strings |
| 11 | Display is ILI9341 320x240 at rotation 90 over SPI with an XPT2046 touch controller, and the trigger button is a TTP223 on `GP167` (header pin 7) -- taken from CLAUDE.md verbatim, never seen in person. | CLAUDE.md ("Upstream platform facts"); no puriyudha code depends on this yet (render/ is a stub) | Visual/physical inspection of the dev kit on 5-6 Aug |
| 12 | The 40-drug seed formulary's dosing ranges (`min_adult_dose`/`max_adult_dose`/`max_daily_dose`) and Tamil/Hindi transliterations were hand-curated from standard, commonly-cited adult oral dosing conventions and standard pharmacy-label transliteration practice -- **not reviewed by a licensed pharmacist or a native Tamil/Hindi speaker.** This is the single highest-stakes unverified claim in this codebase (it feeds CLAUDE.md invariant #2/#3's safety cross-check) and should be the first thing reviewed by a qualified person, on any schedule, not only 5-6 Aug. | `puriyudha/formulary/seed_data.py` | Pharmacist review of every `min_adult_dose`/`max_adult_dose`/`max_daily_dose`/`dose_unit` value against a current Indian formulary reference (e.g. current NLEM or a hospital pharmacy's own reference); native-speaker review of every `tamil_name`/`hindi_name` |

## How to use this file

When a row is settled, update the table in place: replace "Command to
settle" with the actual result and the date it was confirmed, and fix
whatever code/comment encoded the wrong assumption. Do not delete settled
rows -- keep them as a record of what was assumption-vs-verified and when
each was resolved.
