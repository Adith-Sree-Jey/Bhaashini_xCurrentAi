# Findings from the fix-and-re-baseline verification pass (2026-08-04)

T0 and T1 were built and believed complete, but a manual verification pass
(preceding this document) found three real defects, referred to below as
H1, H2, H3. This document records each: what was wrong, why it mattered,
and exactly what changed to fix it. (A fourth, blocking issue -- the test
suite not actually running -- is recorded separately, in the "Part 1"
section below, since it isn't a product-behaviour finding the same way
H1-H3 are; it's a defect in the verification process itself.)

## Part 1 (blocking): the test suite did not actually run

**Severity:** blocking -- nothing else in this document could be trusted
until this was fixed, because the client tests (`test_bhashini_client.py`,
`test_ollama_client.py`) had never executed even once.

**What was wrong:** `python -m pytest` from a bare host interpreter (no
editable install active) failed to collect two test files with
`ModuleNotFoundError: No module named 'tests._fake_http'`, and silently
SKIPPED the one test proving the vendored platform imports at all.

**Root cause, diagnosed (not assumed):** this machine's global Python
install has an unrelated third-party package literally named `tests`
installed in site-packages (a packaging accident in some other project,
unrelated to puriyudha). Because this repo's own `tests/` directory had no
`__init__.py`, Python treated it as an implicit PEP 420 namespace-package
*portion* -- and CPython's import resolution does not stop at a namespace
portion, it keeps scanning `sys.path` for a *regular* package (one with a
loader) and returns that instead, regardless of where in `sys.path` order
it appears. The site-packages `tests` package has a real `__init__.py`, so
`import tests` (and therefore `from tests._fake_http import ...`) silently
resolved to the wrong package. Verified by reproducing the exact original
error after re-removing `tests/__init__.py`, and by confirming
`import tests` resolved to the site-packages path.

**What changed:**
- `tests/__init__.py` added, making this repo's `tests/` a real package
  (ends the namespace-portion ambiguity).
- `pythonpath = ["."]` added to `pyproject.toml`'s
  `[tool.pytest.ini_options]`, so the repo root is at the front of
  `sys.path` before collection, regardless of invoking interpreter or cwd.
- `tests/test_vendor_import.py` changed from `pytest.importorskip` (silent
  skip) to failing loudly by default, with an explicit
  `PURIYUDHA_ALLOW_MISSING_VENDOR=1` opt-out (documented in
  `docs/dev_environment.md`) -- a skip that happens by default hides a
  broken dev environment as a non-failure.
- `tests/test_suite_integrity.py` added: asserts every `tests/test_*.py`
  file is importable, a fresh `pytest --collect-only` exits 0, and the
  collected count stays above a floor constant -- so a future regression
  of this same class (a file silently dropping out of collection) cannot
  happen unnoticed again.
- Portability audit: one unquoted shell variable found and fixed
  (`scripts/setup_dev.sh`'s printed `source $VENV_DIR/...` hint); every
  other path in the repo already used `pathlib.Path` or quoted shell
  variables.

**Verified:** `pocketinfer` was actually installed into `.venv` (submodule
checked out, `pip install --no-deps -e vendor/suno-sutra-sw/python` plus
`constraints/x86-dev-requirements.txt`) so the fix could be verified
against a fully green run, not just an opted-out one. `python -m pytest`
at that point in the pass (before parts 2-6 added more tests):
**117 passed, 0 errors, 0 skipped.** (232 by the end of the full pass --
see `docs/STATUS.md` for the current total.)

## H1: the mock ASR was too clean

**Severity:** High. **Where:** `services/mock_bhashini/fake_backends.py`.

**What was wrong:** `_CANNED_PHRASES` contained only tidy, grammatical,
fully-punctuated, complete sentences ("take one tablet after food").
Everything downstream that will eventually consume ASR output -- the sig
parser (T3) especially -- would have been built and tested exclusively
against input that cannot occur in the field.

**What changed:**
- `_CANNED_PHRASES` split into `_CLEAN_PHRASES` (the old tidy set) and a
  new `_MESSY_PHRASES`: 8 documented categories (no punctuation /
  inconsistent casing, filler and hesitation, repeated/stuttered words,
  dropped words, code-switching, numerals as words and vice versa,
  mid-word truncation, empty/whitespace-only) x >= 2 examples each x 3
  languages, with native-script and Latin-transliteration variants for
  Hindi and Tamil (the actual on-device script is unverified -- see
  `docs/ASSUMPTIONS.md` row 4).
- `fake_asr_transcribe(..., pool="mixed")`: `pool` selects `clean`,
  `messy`, or `mixed` (clean + messy combined, the default).
- `services.mock_bhashini`'s server gained a `--messy
  {clean,messy,mixed}` flag (server default: `mixed`); the test suite's
  own convenience wrapper defaults to `messy` (the hostile path is the
  default under test, per the task's explicit instruction).
- `OllamaClient.generate_json` tolerant-JSON handling (a second, related
  robustness gap in the same "real model output is messier than our
  fixtures assumed" family): repairs a fenced ```` ```json ```` block, a
  prose preamble, or concatenated objects via a balanced-brace scan
  (`_extract_first_balanced_json_object` -- not a regex, deliberately, per
  the task's explicit instruction), logging a WARNING when it repairs
  (a rate worth tracking as a prompt-quality signal). Five new fixtures
  under `tests/fixtures/ollama/`.

**Verified:** `python -m services.mock_bhashini --messy messy`, hit
manually across 7 different inputs, returned visibly messy transcripts
every time (no punctuation, casing noise, code-switching, truncation --
see the session transcript). All new tests pass:
`tests/test_mock_bhashini_backends.py`, `tests/test_ollama_client.py`.

## H2: timeout and retry policy was wrong for a localhost service

**Severity:** High (not Medium -- see CLAUDE.md's rationale). **Where:**
`puriyudha/clients/bhashini.py`, `puriyudha/clients/ollama.py`.

**What was wrong:** `DEFAULT_TIMEOUT_SECONDS = 5.0`, `DEFAULT_RETRIES = 2`,
timeout applied per attempt, linear backoff. Worst case for one call was
roughly 15s; a single patient session makes at least four Bhashini calls
(ASR, NMT inbound, NMT outbound, TTS), compounding to roughly a minute
against a 12-second end-to-end product target. `bhashini_models.service`
is a localhost systemd unit, not a remote API -- a 5xx means the model is
loading or the box is out of memory, and retrying repeatedly over many
seconds does not fix either condition, it just makes the device look
frozen to a patient standing at the counter.

**What changed:**
- Renamed `DEFAULT_TIMEOUT_SECONDS` -> `DEFAULT_PER_ATTEMPT_TIMEOUT_SECONDS`
  in both clients, to make "this is one attempt's ceiling, not the whole
  session's" unambiguous.
- `MAX_RETRIES = 1` (was effectively 2): exactly one fast retry, only for
  connection errors and 5xx; 4xx is never retried (unchanged behaviour,
  now with an explicit test per status code:
  `test_no_4xx_status_code_is_ever_retried`).
- `RETRY_BACKOFF_SECONDS = 0.25`, a short FIXED backoff (was linear,
  scaled by attempt number) -- there is nothing to "back off" from on
  localhost.
- New `puriyudha/clients/deadline.py`: a monotonic-clock `Deadline`,
  constructed once per patient session with a total budget
  (`DEFAULT_SESSION_BUDGET_SECONDS = 12.0`, tied directly to CLAUDE.md's
  end-to-end target), threaded through both `BhashiniClient` and
  `OllamaClient`. Each request's timeout is derived as
  `min(per_call_timeout, deadline.remaining())`; once exhausted, a
  distinct typed exception (`BhashiniDeadlineExceeded` /
  `OllamaDeadlineExceeded`) is raised instead of attempting a request with
  no time left, so callers can degrade gracefully instead of treating it
  as a generic transport failure.

**Verified:** `test_worst_case_elapsed_time_for_fully_failing_nmt_call_is_bounded`
asserts real wall time stays under `MAX_RETRIES * RETRY_BACKOFF_SECONDS +
1.0s` (a few hundred milliseconds in practice) instead of the old ~15s.
Deadline-exhaustion tests use a fake clock (zero real sleep) and assert
`BhashiniDeadlineExceeded`/`OllamaDeadlineExceeded` is raised rather than a
request hanging. Full suite (`tests/test_bhashini_client.py`,
`tests/test_ollama_client.py`, `tests/test_deadline.py`) green; full repo
suite still completes in single-digit seconds.

## H3: unconfirmed output was not structurally blocked from patient-facing code

**Severity:** High. **Where:** `puriyudha/schema.py`, `puriyudha/render/`,
`puriyudha/issue/`.

**What was wrong:** `validate()` could return `NeedsHumanConfirmation`,
but nothing consumed that distinction -- a low-confidence (or
never-validated) `MedicationOrder` was structurally indistinguishable from
a confirmed one to any function that might render or speak it. This was
harmless only because `render/` and `issue/` were empty stubs; it would
not have stayed harmless once T5/T6 were written.

**What changed:**
- New `ConfirmedOrder` type in `puriyudha/schema.py`, wrapping a
  `MedicationOrder`. Enforcement mechanism: a private constructor token
  (`_CONFIRMED_ORDER_TOKEN`, a module-private sentinel object) -- the only
  legitimate caller is `validate()`, in the same module. Documented in the
  class's own docstring, per the task's explicit requirement.
- `validate()` now returns a `ConfirmedOrder` (instead of the bare order)
  when confidence clears the threshold everywhere.
- New `puriyudha.schema.require_confirmed_order()`: a runtime backstop for
  the type hint, since Python does not enforce annotations at runtime.
- Stub signatures added to `puriyudha/render/__init__.py`
  (`render_explanation`, `render_screen_frame`) and
  `puriyudha/issue/__init__.py` (`issue_qr_payload`,
  `issue_audio_artifact`), all typed `order: ConfirmedOrder` and calling
  `require_confirmed_order()` as their first line, so T5/T6 cannot be
  written the wrong way by accident.
- New CLAUDE.md invariant text (under invariant #3) codifying the rule:
  every patient-facing function -- anything in `render/`, `issue/`, or
  anything producing speech, a screen frame, or a QR payload -- takes a
  `ConfirmedOrder`, never a bare `MedicationOrder`.

**Verified:** `tests/test_patient_facing_stubs.py` proves a bare
`MedicationOrder` (even a high-confidence one) is rejected with a
`TypeError` by every current patient-facing stub, that a genuine
`ConfirmedOrder` clears the guard, and that the signature itself is
annotated `ConfirmedOrder` (resolved via `typing.get_type_hints`, since
`from __future__ import annotations` makes raw annotations strings).
`tests/test_schema.py` proves a low-confidence order (overall or
per-field) never yields a `ConfirmedOrder`, and that
`DEFAULT_CONFIDENCE_THRESHOLD` is the only literal of its value anywhere
in `puriyudha/schema.py` (AST-based check, not a text grep).
