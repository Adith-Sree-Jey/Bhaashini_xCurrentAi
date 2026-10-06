# data/

Generated and vendored data artifacts. Nothing in this directory is source
code; regenerate it with the tools in `tools/` rather than hand-editing.

- `formulary.sqlite` (generated) - offline drug formulary database, built
  from the 40-drug hand-curated seed set in
  `puriyudha/formulary/seed_data.py` (the primary deliverable of
  `puriyudha/formulary/`; a full NLEM/Jan Aushadhi ingest is secondary and
  not yet built). Generate it with:
  ```
  python -m puriyudha.formulary --build
  ```
- `fonts/` (not yet added) - Tamil and Hindi fonts for the pictogram
  renderer. The upstream UI only ships `NotoSansDevanagari` `.pcf` fonts;
  Tamil support does not exist upstream and must be added here.
- `pictograms/` (not yet added) - artwork used by `puriyudha/render/`.
- `fixtures/` - recorded ASR/NMT/TTS/OCR fixtures used by hermetic tests and
  the mock Bhashini service (`services/mock_bhashini/`). No PHI may be
  committed here, ever - see CLAUDE.md invariant #4.
- `eval/` (not yet added) - evaluation sets for `tools/evaluate`.
