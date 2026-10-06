# Status

Last updated: 2026-08-04, after the fix-and-re-baseline pass described in
`docs/FINDINGS.md`.

## Done and verified

- **Test suite actually runs.** `python -m pytest` collects and runs every
  test, from any interpreter (host or venv), in any working directory,
  including one containing a space, with 0 collection errors and 0 skips
  when the vendored platform is installed. Root cause of the prior
  breakage (a same-named `tests` package elsewhere on `sys.path` shadowing
  this repo's own `tests/`) diagnosed and fixed -- see
  `tests/__init__.py` and `pyproject.toml`'s `pythonpath` setting.
- **Vendored platform (`pocketinfer`) is actually installed and importable**
  in `.venv`, for real, on this Windows dev machine -- not skipped, not
  opted out. `vendor/suno-sutra-sw` is checked out at the pinned commit
  (`89487b718da9eddbc302d93e2a04e79522c67bc3`) and `pip install --no-deps -e
  vendor/suno-sutra-sw/python` plus `constraints/x86-dev-requirements.txt`
  both succeeded, including `pyaudio` and `vosk` via prebuilt wheels.
- **`tests/test_vendor_import.py` fails loudly by default** when
  `pocketinfer` is missing, instead of silently skipping (opt-out:
  `PURIYUDHA_ALLOW_MISSING_VENDOR=1`, documented in
  `docs/dev_environment.md`).
- **Messy ASR fixtures** (`services/mock_bhashini/fake_backends.py`):
  8 documented hostile-input categories x >= 2 examples x 3 languages,
  native script + Latin transliteration for hi/ta. `--messy
  {clean,messy,mixed}` flag on the mock server, default `mixed`; test
  suite defaults to `messy`.
- **Ollama malformed-JSON handling**: `OllamaClient.generate_json` repairs
  a fenced code block / prose preamble / concatenated objects via a
  balanced-brace scan (`_extract_first_balanced_json_object`), logs a
  WARNING when it does, and still retry-once-then-raises for genuinely
  malformed input (trailing comma, mid-string truncation). Five new
  fixtures under `tests/fixtures/ollama/`.
- **Timeout/retry/deadline redesign** (`puriyudha/clients/deadline.py`):
  exactly one retry (5xx/connection errors only, never 4xx), a short fixed
  backoff, and a shared, monotonic-clock `Deadline` (default 12s session
  budget, CLAUDE.md's end-to-end target) threaded through both
  `BhashiniClient` and `OllamaClient`. `BhashiniDeadlineExceeded` /
  `OllamaDeadlineExceeded` raised instead of hanging once the budget is
  exhausted. Worst-case wall time for a fully-failing call is bounded and
  tested.
- **`ConfirmedOrder` type gate** (`puriyudha/schema.py`): only
  `validate()` can construct one (private constructor token). Every
  current patient-facing stub (`puriyudha/render`, `puriyudha/issue`)
  takes `ConfirmedOrder`, never a bare `MedicationOrder`, enforced both by
  the type hint and by a runtime guard
  (`puriyudha.schema.require_confirmed_order`).
- **LLM fallback cut from the critical path**: `puriyudha.config.PipelineConfig.llm_enabled`
  defaults to `False`. `puriyudha.extract.get_ollama_client` is the only
  gate to `OllamaClient`, importing it lazily so the disabled path never
  puts `puriyudha.clients.ollama` in `sys.modules` at all (proved from a
  fresh subprocess in `tests/test_extract.py`). `OllamaClient` itself
  stays fully implemented and tested. `puriyudha.runtime.metrics` is the
  slot-level F1 delta plumbing for the future T7 evaluation harness.
- **Repo hygiene**: `.gitmodules` already used the public GitHub URL (no
  fix needed, just confirmed); one unquoted path in `scripts/setup_dev.sh`
  fixed; a regression test (`tests/test_path_with_space.py`) proves the
  suite passes when run from a path containing a space, not just an
  anecdote about this one checkout's location.
- **Formulary seed set** (`puriyudha/formulary/`, now the PRIMARY
  deliverable of that module): 40 hand-curated common Indian outpatient
  drugs (`seed_data.py`) with standard adult oral dose ranges, Tamil/Hindi
  transliterations, and a handful of high-confidence Indian brand names;
  a SQLite build step (`db.py`) and a case-insensitive fuzzy/brand-name/
  typo-tolerant matcher (`matcher.py`, rapidfuzz). **Not yet reviewed by a
  pharmacist or a native Tamil/Hindi speaker -- see `docs/ASSUMPTIONS.md`
  row 12, the highest-stakes open item in this file.** Full NLEM/Jan
  Aushadhi ingest is secondary, not yet built.
- **Sig parser** (`puriyudha/sig/`), now the ONLY extraction path (the LLM
  fallback is cut from the critical path -- see above): resolves
  `dose_qty`/`frequency`/`food_relation`/`duration_days`/`prn` from
  English clinician dosing shorthand via deterministic regex/keyword
  rules, never drug identity (CLAUDE.md #2 -- that's `puriyudha.formulary`
  and, eventually, OCR). Tested primarily against the messy "en" ASR pool
  (`tests/test_sig.py`, one hand-verified expected outcome per phrase),
  not clean text, plus adversarial/out-of-range input. A field that
  cannot be fully resolved is never silently defaulted or dropped --
  `SigParseResult.to_needs_confirmation()` is the only path from an
  incomplete parse onward, converting it to a `NeedsHumanConfirmation`.

Full suite: **403 passed** (venv, `pocketinfer` installed) in ~14s. See
`docs/FINDINGS.md` for what changed and why, part by part.

## In progress / next command

Nothing is mid-flight. The literal next command for a new contributor
picking this up:

```
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pytest
```

should show `403 passed`, 0 errors, 0 skips.

The next substantive work is a merge step that combines `puriyudha.sig`'s
schedule output with `puriyudha.formulary`'s drug-identity match into an
actual `MedicationOrder` (nothing currently does this -- each module is
tested and correct in isolation, but there is no glue between them yet),
then T5 (render/) and T6 (issue/) -- currently stub signatures only,
gated to take `ConfirmedOrder` (see `docs/ASSUMPTIONS.md` row 11 for the
one hardware fact render/ will need: the ILI9341 320x240 rotation-90
display).

## Open findings (this pass)

See `docs/FINDINGS.md` for full detail. Summary:

- **H1** (mock ASR too clean) -- fixed, part 2.
- **H2** (timeout/retry policy wrong for a localhost service) -- fixed,
  part 3.
- **H3** (unconfirmed output not structurally blocked from patient-facing
  code) -- fixed, part 4.

No new findings opened during this pass beyond what was already scoped.

## Blocked on

- **The Jetson itself**, until 5 Aug 2026. Every row in
  `docs/ASSUMPTIONS.md` is blocked on this, most urgently: the real
  `:11400` contract (row 1-3), Hindi/Tamil ASR script (row 4), and whether
  the shipped SD image (if any) matches our submodule pin (row 6-7).
- **T5/T6 real implementations** (render/, issue/) are blocked on nothing
  code-related -- the stubs and type gates are ready -- but have no reason
  to be written yet ahead of the sig+formulary merge step above producing
  a real `MedicationOrder` to render. `puriyudha.sig` and
  `puriyudha.formulary` are themselves both implemented and tested now,
  just not yet wired together.
- **T7 evaluation harness** is blocked on the same merge step (something
  end-to-end to evaluate) -- `puriyudha.runtime.metrics` (the F1-delta
  plumbing) is ready and waiting for it.
