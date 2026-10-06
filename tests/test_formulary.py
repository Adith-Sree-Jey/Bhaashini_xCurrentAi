"""Tests for puriyudha.formulary: seed-data integrity, SQLite build/load
round-trip, and the fuzzy matcher.

Hermetic: SQLite databases are built under pytest's tmp_path, never at
data/formulary.sqlite (data/README.md: generated artifacts are
regenerated, not committed, and tests must not depend on -- or leave
behind -- that file).
"""
from pathlib import Path

import pytest

from puriyudha.formulary import (
    DEFAULT_MIN_SCORE,
    MAX_MATCHES,
    FormularyDrug,
    build_formulary_db,
    load_formulary_db,
    match_drug_name,
)
from puriyudha.formulary.seed_data import SEED_DRUGS


def make_drug(**overrides) -> FormularyDrug:
    kwargs = dict(
        generic_name="testdrugium",
        display_name="Testdrugium",
        tamil_name="டெஸ்ட்ட்ரக்கியம்",
        hindi_name="टेस्टड्रगियम",
        category="test-category",
        forms=("tablet",),
        common_strengths=("100 mg",),
        dose_unit="mg",
        min_adult_dose=100.0,
        max_adult_dose=200.0,
        max_daily_dose=400.0,
        typical_frequency_per_day=(2,),
        route="oral",
    )
    kwargs.update(overrides)
    return FormularyDrug(**kwargs)


# --- FormularyDrug validation --------------------------------------------------

def test_valid_drug_constructs():
    drug = make_drug()
    assert drug.generic_name == "testdrugium"


def test_generic_name_must_be_lowercase():
    with pytest.raises(ValueError, match="lowercase"):
        make_drug(generic_name="Testdrugium")


def test_generic_name_must_not_be_empty():
    with pytest.raises(ValueError, match="lowercase"):
        make_drug(generic_name="")


def test_tamil_name_must_not_be_empty():
    with pytest.raises(ValueError, match="tamil_name"):
        make_drug(tamil_name="")


def test_hindi_name_must_not_be_empty():
    with pytest.raises(ValueError, match="hindi_name"):
        make_drug(hindi_name="")


def test_forms_must_not_be_empty():
    with pytest.raises(ValueError, match="forms"):
        make_drug(forms=())


def test_common_strengths_must_not_be_empty():
    with pytest.raises(ValueError, match="common_strengths"):
        make_drug(common_strengths=())


def test_dose_unit_must_be_valid():
    with pytest.raises(ValueError, match="dose_unit"):
        make_drug(dose_unit="grams")


def test_min_adult_dose_must_be_positive():
    with pytest.raises(ValueError, match="min_adult_dose"):
        make_drug(min_adult_dose=0.0)


def test_max_adult_dose_must_be_at_least_min():
    with pytest.raises(ValueError, match="max_adult_dose"):
        make_drug(min_adult_dose=200.0, max_adult_dose=100.0)


def test_max_adult_dose_equal_to_min_is_allowed():
    drug = make_drug(min_adult_dose=100.0, max_adult_dose=100.0)
    assert drug.max_adult_dose == 100.0


def test_max_daily_dose_must_be_at_least_max_adult_dose():
    with pytest.raises(ValueError, match="max_daily_dose"):
        make_drug(max_adult_dose=200.0, max_daily_dose=100.0)


def test_max_daily_dose_none_is_allowed():
    drug = make_drug(max_daily_dose=None)
    assert drug.max_daily_dose is None


def test_typical_frequency_per_day_must_not_be_empty():
    with pytest.raises(ValueError, match="typical_frequency_per_day"):
        make_drug(typical_frequency_per_day=())


def test_typical_frequency_per_day_entries_must_be_positive():
    with pytest.raises(ValueError, match="typical_frequency_per_day"):
        make_drug(typical_frequency_per_day=(0,))


def test_route_must_not_be_empty():
    with pytest.raises(ValueError, match="route"):
        make_drug(route="")


def test_common_brand_names_defaults_to_empty_tuple():
    drug = make_drug()
    assert drug.common_brand_names == ()


# --- FormularyDrug.dose_in_range / all_names ------------------------------------

def test_dose_in_range_accepts_bounds_inclusively():
    drug = make_drug(min_adult_dose=100.0, max_adult_dose=200.0)
    assert drug.dose_in_range(100.0) is True
    assert drug.dose_in_range(200.0) is True
    assert drug.dose_in_range(150.0) is True


def test_dose_in_range_rejects_outside_bounds():
    drug = make_drug(min_adult_dose=100.0, max_adult_dose=200.0)
    assert drug.dose_in_range(99.0) is False
    assert drug.dose_in_range(201.0) is False


def test_all_names_includes_generic_display_and_brands():
    drug = make_drug(
        generic_name="paracetamol",
        display_name="Paracetamol",
        common_brand_names=("Dolo", "Crocin"),
    )
    assert drug.all_names() == ("paracetamol", "Paracetamol", "Dolo", "Crocin")


def test_all_names_excludes_tamil_hindi_names():
    drug = make_drug(tamil_name="தமிழ்பெயர்", hindi_name="हिन्दीनाम")
    assert "தமிழ்பெயர்" not in drug.all_names()
    assert "हिन्दीनाम" not in drug.all_names()


# --- SEED_DRUGS integrity -----------------------------------------------------

def test_seed_drugs_has_exactly_40_entries():
    assert len(SEED_DRUGS) == 40


def test_seed_drugs_generic_names_are_unique():
    names = [d.generic_name for d in SEED_DRUGS]
    assert len(names) == len(set(names)), "duplicate generic_name in SEED_DRUGS"


def test_seed_drugs_all_names_are_globally_unique():
    """No two different drugs should share a name (generic/display/brand)
    -- if they did, the fuzzy matcher's index-based disambiguation would
    still work correctly (see matcher.py), but it would be a sign of a
    genuine data error (e.g. a brand name copy-pasted onto the wrong
    drug)."""
    seen = {}
    for drug in SEED_DRUGS:
        for name in drug.all_names():
            assert name not in seen, (
                f"name {name!r} appears on both {seen.get(name)!r} and {drug.generic_name!r}"
            )
            seen[name] = drug.generic_name


def test_seed_drugs_all_routes_are_oral():
    """The part-6 seed set is deliberately outpatient-oral-only (CLAUDE.md:
    discharge-counter context) -- this test exists so adding a
    non-oral entry later is a deliberate, visible decision, not an
    accidental one."""
    assert all(d.route == "oral" for d in SEED_DRUGS)


def test_seed_drugs_does_not_include_ranitidine():
    """Ranitidine was withdrawn/restricted over NDMA contamination -- see
    seed_data.py's module docstring. This is a permanent regression guard,
    not a one-off check."""
    assert "ranitidine" not in {d.generic_name for d in SEED_DRUGS}
    assert "Ranitidine" not in {name for d in SEED_DRUGS for name in d.all_names()}


@pytest.mark.parametrize("drug", SEED_DRUGS, ids=lambda d: d.generic_name)
def test_every_seed_drug_min_dose_at_or_below_typical_strength_ceiling(drug):
    """Sanity check, not a clinical proof: a formulary entry whose minimum
    single dose is astronomically larger than anything on its own
    strengths list would indicate a units/data-entry mistake (e.g. writing
    a daily total into the per-dose field). Loose bound deliberately (10x
    headroom) -- this is a smoke test, not a dosing calculator."""
    assert drug.min_adult_dose <= drug.max_adult_dose
    if drug.max_daily_dose is not None:
        assert drug.max_daily_dose <= drug.max_adult_dose * 10


# --- SQLite build / load round-trip ---------------------------------------------

def test_build_formulary_db_returns_row_count(tmp_path):
    db_path = tmp_path / "formulary.sqlite"
    count = build_formulary_db(SEED_DRUGS, db_path)
    assert count == 40
    assert db_path.exists()


def test_load_formulary_db_round_trips_every_field(tmp_path):
    db_path = tmp_path / "formulary.sqlite"
    build_formulary_db(SEED_DRUGS, db_path)
    loaded = load_formulary_db(db_path)
    assert len(loaded) == 40

    by_name = {d.generic_name: d for d in loaded}
    original = next(d for d in SEED_DRUGS if d.generic_name == "paracetamol")
    round_tripped = by_name["paracetamol"]
    assert round_tripped == original  # frozen dataclass equality, every field


def test_load_formulary_db_missing_file_raises_with_build_hint(tmp_path):
    missing = tmp_path / "does_not_exist.sqlite"
    with pytest.raises(FileNotFoundError, match="--build"):
        load_formulary_db(missing)


def test_build_formulary_db_overwrites_by_default(tmp_path):
    db_path = tmp_path / "formulary.sqlite"
    build_formulary_db(SEED_DRUGS[:5], db_path)
    assert len(load_formulary_db(db_path)) == 5
    build_formulary_db(SEED_DRUGS, db_path)
    assert len(load_formulary_db(db_path)) == 40


def test_build_formulary_db_overwrite_false_raises_if_exists(tmp_path):
    db_path = tmp_path / "formulary.sqlite"
    build_formulary_db(SEED_DRUGS, db_path)
    with pytest.raises(FileExistsError):
        build_formulary_db(SEED_DRUGS, db_path, overwrite=False)


def test_build_formulary_db_rejects_duplicate_generic_name(tmp_path):
    db_path = tmp_path / "formulary.sqlite"
    dupes = (make_drug(generic_name="dup"), make_drug(generic_name="dup"))
    with pytest.raises(ValueError, match="duplicate generic_name"):
        build_formulary_db(dupes, db_path)


def test_build_formulary_db_creates_parent_directories(tmp_path):
    db_path = tmp_path / "nested" / "dir" / "formulary.sqlite"
    build_formulary_db(SEED_DRUGS, db_path)
    assert db_path.exists()


# --- fuzzy matcher --------------------------------------------------------------

def test_match_exact_generic_name():
    matches = match_drug_name("paracetamol", SEED_DRUGS)
    assert matches
    assert matches[0].drug.generic_name == "paracetamol"
    assert matches[0].score == pytest.approx(100.0)


def test_match_is_case_insensitive():
    matches = match_drug_name("PARACETAMOL", SEED_DRUGS)
    assert matches
    assert matches[0].drug.generic_name == "paracetamol"


def test_match_brand_name():
    matches = match_drug_name("dolo", SEED_DRUGS)
    assert matches
    assert matches[0].drug.generic_name == "paracetamol"
    assert matches[0].matched_name == "Dolo"


def test_match_tolerates_a_realistic_typo():
    matches = match_drug_name("amoxicilin", SEED_DRUGS)  # missing one 'l'
    assert matches
    assert matches[0].drug.generic_name == "amoxicillin"


def test_match_returns_empty_for_unrelated_text():
    matches = match_drug_name("xyzabc123nonsense", SEED_DRUGS)
    assert matches == []


def test_match_returns_empty_for_blank_query():
    assert match_drug_name("", SEED_DRUGS) == []
    assert match_drug_name("   ", SEED_DRUGS) == []


def test_match_returns_empty_for_empty_formulary():
    assert match_drug_name("paracetamol", []) == []


def test_match_never_exceeds_max_matches():
    matches = match_drug_name("a", SEED_DRUGS, min_score=0.0)
    assert len(matches) <= MAX_MATCHES


def test_match_respects_custom_max_matches():
    matches = match_drug_name("a", SEED_DRUGS, min_score=0.0, max_matches=1)
    assert len(matches) <= 1


def test_match_respects_min_score_threshold():
    loose = match_drug_name("paracetamol", SEED_DRUGS, min_score=0.0)
    strict = match_drug_name("paracetamol", SEED_DRUGS, min_score=99.9)
    assert len(strict) <= len(loose)
    assert all(m.score >= 99.9 for m in strict)


def test_match_never_returns_the_same_drug_twice():
    matches = match_drug_name("paracetamol", SEED_DRUGS, min_score=0.0, max_matches=40)
    generic_names = [m.drug.generic_name for m in matches]
    assert len(generic_names) == len(set(generic_names))


def test_default_min_score_matches_module_constant():
    assert DEFAULT_MIN_SCORE == 70.0
