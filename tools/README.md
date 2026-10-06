# tools/

CLI utilities for developing and evaluating puriyudha, as opposed to the
on-device runtime in `puriyudha/`. Planned:

- `label` - annotate captured prescription images / audio for eval fixtures.
- `evaluate` - run the pipeline against `data/eval/` and report accuracy,
  refusal rate, and per-stage timing.
- `report` - summarise a run's JSONL timing logs.

None of these are implemented yet.
