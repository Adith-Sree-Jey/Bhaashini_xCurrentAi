"""SQLite persistence for the formulary: build a database from
`FormularyDrug` entries, and load it back.

One table (`drugs`), one row per drug. List-shaped fields (`forms`,
`common_strengths`, `typical_frequency_per_day`, `common_brand_names`) are
stored as JSON text columns. A normalized schema (separate forms/
strengths tables) would make more sense once this scales past a
hand-curated 40-drug seed set toward the full NLEM/Jan Aushadhi ingest
(secondary, not yet built) -- for 40 rows, JSON columns are simpler and
exactly as fast.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Union

from puriyudha.formulary.models import FormularyDrug

SCHEMA_VERSION = 1

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS drugs (
    generic_name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    tamil_name TEXT NOT NULL,
    hindi_name TEXT NOT NULL,
    category TEXT NOT NULL,
    forms TEXT NOT NULL,
    common_strengths TEXT NOT NULL,
    dose_unit TEXT NOT NULL,
    min_adult_dose REAL NOT NULL,
    max_adult_dose REAL NOT NULL,
    max_daily_dose REAL,
    typical_frequency_per_day TEXT NOT NULL,
    route TEXT NOT NULL,
    common_brand_names TEXT NOT NULL
)
"""

_INSERT_SQL = """
INSERT INTO drugs (
    generic_name, display_name, tamil_name, hindi_name, category, forms,
    common_strengths, dose_unit, min_adult_dose, max_adult_dose,
    max_daily_dose, typical_frequency_per_day, route, common_brand_names
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_ALL_SQL = """
SELECT generic_name, display_name, tamil_name, hindi_name, category, forms,
       common_strengths, dose_unit, min_adult_dose, max_adult_dose,
       max_daily_dose, typical_frequency_per_day, route, common_brand_names
FROM drugs
ORDER BY generic_name
"""


def _row_from_drug(drug: FormularyDrug) -> tuple:
    return (
        drug.generic_name,
        drug.display_name,
        drug.tamil_name,
        drug.hindi_name,
        drug.category,
        json.dumps(list(drug.forms)),
        json.dumps(list(drug.common_strengths)),
        drug.dose_unit,
        drug.min_adult_dose,
        drug.max_adult_dose,
        drug.max_daily_dose,
        json.dumps(list(drug.typical_frequency_per_day)),
        drug.route,
        json.dumps(list(drug.common_brand_names)),
    )


def _drug_from_row(row: tuple) -> FormularyDrug:
    (
        generic_name,
        display_name,
        tamil_name,
        hindi_name,
        category,
        forms,
        common_strengths,
        dose_unit,
        min_adult_dose,
        max_adult_dose,
        max_daily_dose,
        typical_frequency_per_day,
        route,
        common_brand_names,
    ) = row
    return FormularyDrug(
        generic_name=generic_name,
        display_name=display_name,
        tamil_name=tamil_name,
        hindi_name=hindi_name,
        category=category,
        forms=tuple(json.loads(forms)),
        common_strengths=tuple(json.loads(common_strengths)),
        dose_unit=dose_unit,
        min_adult_dose=min_adult_dose,
        max_adult_dose=max_adult_dose,
        max_daily_dose=max_daily_dose,
        typical_frequency_per_day=tuple(json.loads(typical_frequency_per_day)),
        route=route,
        common_brand_names=tuple(json.loads(common_brand_names)),
    )


def build_formulary_db(
    drugs: Iterable[FormularyDrug], db_path: Union[str, Path], *, overwrite: bool = True
) -> int:
    """Builds a fresh SQLite database at `db_path` from `drugs`. Returns
    the number of rows written.

    Raises `ValueError` if `drugs` contains a duplicate `generic_name`
    (the table's primary key) -- caught here, deliberately, with the exact
    offending name, rather than surfacing as an opaque
    `sqlite3.IntegrityError` partway through the insert.

    `overwrite`: if True (default), any existing file at `db_path` is
    replaced entirely -- this is a BUILD step (data/README.md: "generated
    ... regenerate, don't commit"), not an incremental update. If False,
    raises `FileExistsError` instead of silently overwriting.
    """
    db_path = Path(db_path)
    if db_path.exists():
        if not overwrite:
            raise FileExistsError(f"{db_path} already exists (pass overwrite=True to replace it)")
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    drugs = list(drugs)
    seen = set()
    for drug in drugs:
        if drug.generic_name in seen:
            raise ValueError(f"duplicate generic_name in formulary source data: {drug.generic_name!r}")
        seen.add(drug.generic_name)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_CREATE_TABLE_SQL)
        conn.executemany(_INSERT_SQL, (_row_from_drug(d) for d in drugs))
        conn.commit()
    finally:
        conn.close()
    return len(drugs)


def load_formulary_db(db_path: Union[str, Path]) -> List[FormularyDrug]:
    """Loads every drug from the SQLite database at `db_path`, sorted by
    `generic_name`.

    Raises `FileNotFoundError` (naming the build command) if `db_path`
    does not exist, rather than a confusing `sqlite3.OperationalError`.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"formulary database not found at {db_path} -- build it first: "
            "python -m puriyudha.formulary --build"
        )
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(_SELECT_ALL_SQL).fetchall()
    finally:
        conn.close()
    return [_drug_from_row(row) for row in rows]
