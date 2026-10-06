"""CLI entry point: `python -m puriyudha.formulary --build` or `--demo`.

See the package docstring (`puriyudha/formulary/__init__.py`) and
README.md's "puriyudha.formulary" section for the exact expected output.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from puriyudha.formulary.db import build_formulary_db, load_formulary_db
from puriyudha.formulary.matcher import match_drug_name
from puriyudha.formulary.seed_data import SEED_DRUGS

# Tamil and Devanagari output (the whole point of --demo) will raise
# UnicodeEncodeError on a Windows console using a legacy codepage (cp1252
# etc.) unless stdout is explicitly UTF-8. The real target (Jetson/JetPack
# Linux) has a UTF-8 locale by default; this only matters for the x86 dev
# laptop this is built and demoed on before 5 Aug 2026 (CLAUDE.md).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_DB_PATH = Path("data") / "formulary.sqlite"

#: A mix of a clean generic name, a brand name, two deliberately garbled
#: spellings (an ASR/OCR-realistic typo each), and one clearly unrelated
#: string -- so the demo shows both a confident match and CLAUDE.md
#: invariant #3's refusal path in the same run.
_DEMO_QUERIES = ("paracetamol", "dolo", "augmentin", "amoxicilin", "crocinn", "xyzabc123")


def build(db_path: Path = DEFAULT_DB_PATH) -> int:
    count = build_formulary_db(SEED_DRUGS, db_path)
    print(f"Built {db_path} with {count} drugs.")
    return 0


def demo(db_path: Path = DEFAULT_DB_PATH) -> int:
    if db_path.exists():
        drugs = load_formulary_db(db_path)
        source = f"{db_path} ({len(drugs)} drugs)"
    else:
        drugs = list(SEED_DRUGS)
        source = f"in-memory seed data ({len(drugs)} drugs; {db_path} not built yet -- run --build first for the on-disk path)"
    print(f"puriyudha.formulary demo -- matching against {source}\n")
    for query in _DEMO_QUERIES:
        matches = match_drug_name(query, drugs)
        print(f"  {query!r}:")
        if not matches:
            print("    (no confident match -- would emit NeedsHumanConfirmation)")
        for m in matches:
            print(
                f"    {m.drug.display_name} (matched {m.matched_name!r}, score {m.score:.1f}) "
                f"-- ta: {m.drug.tamil_name}, hi: {m.drug.hindi_name}"
            )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m puriyudha.formulary",
        description="Build data/formulary.sqlite from the hand-curated seed set, or run a matcher demo.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true", help="build data/formulary.sqlite from the seed data")
    group.add_argument("--demo", action="store_true", help="run a few example fuzzy-match queries")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help=f"default: {DEFAULT_DB_PATH}")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.build:
        return build(args.db_path)
    return demo(args.db_path)


if __name__ == "__main__":
    sys.exit(main())
