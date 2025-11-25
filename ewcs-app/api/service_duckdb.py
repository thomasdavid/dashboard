"""
EWCS Dashboard – DuckDB + Pandas backend
"""

from __future__ import annotations

import duckdb
import pandas as pd
from typing import Dict, List, Tuple, Any, Optional
import traceback
import os

from .settings import (
    DATA_FILE,
    ECS_DATA_FILE,
    LABELS_FILE,
    RESPONSE_META_FILE,
    COUNTRY_FILE,
)

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

SURVEY_YEARS = {
    "EWCSR1": 1991, "EWCSR2": 1995, "EWCSR3": 2000, "EWCSR4": 2005,
    "EWCSR5": 2010, "EWCSR6": 2015, "EWCS2021": 2021, "COVID": 2020,
    "ECSR1": 2004, "ECSR2": 2009, "ECSR3": 2013, "ECSR4": 2019
}

ECS_CATEGORIES = ['sector13', 'mm102grp', 'sector2', 'rev_type', 'sec3', 'size_5', 'size_10']
EWCS_CATEGORIES = ['agesex', 'isco']

# ---------------------------------------------------------------------
# Initialise DuckDB
# ---------------------------------------------------------------------

print(f"--- Initialising In-Memory Database...")
_con = duckdb.connect(database=":memory:")

# 1. Load EWCS Data (Ingest into Memory)
if os.path.exists(DATA_FILE):
    print(f"--- Ingesting EWCS data from: {DATA_FILE}")
    ewcs_path = str(DATA_FILE)
else:
    ewcs_path = str(DATA_FILE).replace("data.parquet", "split_*.parquet")
    print(f"--- Ingesting EWCS split data from: {ewcs_path}")

try:
    # OPTIMIZATION: Use CREATE TABLE instead of VIEW to load data into RAM
    _con.execute(f"CREATE OR REPLACE TABLE main_data AS SELECT * FROM read_parquet('{ewcs_path}');")
    
    # OPTIMIZATION: Create Indices for faster filtering
    print("--- Indexing EWCS data...")
    cols = [r[1] for r in _con.execute("PRAGMA table_info(main_data)").fetchall()]
    if 'survey' in cols: _con.execute("CREATE INDEX idx_ewcs_survey ON main_data(survey)")
    if 'question' in cols: _con.execute("CREATE INDEX idx_ewcs_question ON main_data(question)")
    
except Exception as e:
    print(f"!!! Error loading EWCS data: {e}")
    _con.execute("CREATE OR REPLACE TABLE main_data AS SELECT 1 as dummy")

# 2. Load ECS Data (Ingest into Memory)
if os.path.exists(ECS_DATA_FILE):
    print(f"--- Ingesting ECS data from: {ECS_DATA_FILE}")
    try:
        _con.execute(f"CREATE OR REPLACE TABLE ecs_data AS SELECT * FROM read_parquet('{ECS_DATA_FILE}');")
        
        # OPTIMIZATION: Create Indices
        print("--- Indexing ECS data...")
        ecs_cols = [r[1] for r in _con.execute("PRAGMA table_info(ecs_data)").fetchall()]
        if 'survey' in ecs_cols: _con.execute("CREATE INDEX idx_ecs_survey ON ecs_data(survey)")
        if 'question' in ecs_cols: _con.execute("CREATE INDEX idx_ecs_question ON ecs_data(question)")
        
    except Exception as e:
        print(f"!!! Error loading ECS data: {e}")
        _con.execute("CREATE OR REPLACE TABLE ecs_data AS SELECT 1 as dummy")
else:
    print(f"!!! ECS Data file not found: {ECS_DATA_FILE}")
    _con.execute("CREATE OR REPLACE TABLE ecs_data AS SELECT 1 as dummy")

# --- CACHE METADATA ---
print("--- DEBUG: Caching metadata...")
_data_variables = set()
_ecs_surveys = set()

_cols_main_lower = set()
_cols_main_map = {}
_cols_ecs_lower = set()
_cols_ecs_map = {}

try:
    # EWCS Columns
    try:
        r = _con.execute("PRAGMA table_info(main_data)").fetchall()
        _cols_main_lower = {x[1].lower() for x in r}
        _cols_main_map = {x[1].lower(): x[1] for x in r}
        
        if 'question' in _cols_main_lower:
            rows = _con.execute("SELECT DISTINCT question FROM main_data").fetchall()
            _data_variables.update({str(row[0]).lower() for row in rows})
        else:
            _data_variables.update(_cols_main_lower)
    except: pass
        
    # ECS Columns & Surveys
    try:
        r_ecs = _con.execute("PRAGMA table_info(ecs_data)").fetchall()
        _cols_ecs_lower = {x[1].lower() for x in r_ecs}
        _cols_ecs_map = {x[1].lower(): x[1] for x in r_ecs}
        
        if 'survey' in _cols_ecs_lower:
            s_rows = _con.execute("SELECT DISTINCT survey FROM ecs_data").fetchall()
            _ecs_surveys = {str(row[0]) for row in s_rows}
            print(f"--- DEBUG: ECS Surveys found in DB: {_ecs_surveys}")
            
            if 'question' in _cols_ecs_lower:
                q_rows = _con.execute("SELECT DISTINCT question FROM ecs_data").fetchall()
                _data_variables.update({str(row[0]).lower() for row in q_rows})
    except: pass

except Exception as e:
    print(f"!!! Metadata Cache Error: {e}")

# 3. Labels (Load into Memory Tables)
try:
    _con.execute(f"CREATE OR REPLACE TABLE dashboard_labels AS SELECT TRIM(Survey) AS Survey, \"Question Number\", Variable, Question, \"Short\" FROM read_csv('{LABELS_FILE}', auto_detect=True, header=True);")
    _con.execute("CREATE INDEX idx_labels_survey ON dashboard_labels(Survey)")
    _con.execute("CREATE INDEX idx_labels_var ON dashboard_labels(Variable)")
except: pass

try:
    _con.execute(f"CREATE OR REPLACE TABLE response_labels AS SELECT * FROM read_parquet('{RESPONSE_META_FILE}');")
    # Indexing response labels significantly speeds up the 'value_label' lookup
    _con.execute("CREATE INDEX idx_resp_survey_var ON response_labels(survey, variable)")
except: pass

# ---------------------------------------------------------------------
# Country Map
# ---------------------------------------------------------------------
COUNTRY_MAP = {}
try:
    cdf = pd.read_csv(COUNTRY_FILE)
    cdf.columns = cdf.columns.str.strip().str.lower()
    if 'value' in cdf.columns and 'label' in cdf.columns:
        for _, row in cdf.iterrows():
            try: COUNTRY_MAP[int(row["value"])] = row["label"]
            except: continue
except: pass

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _map_country_label(survey: str, code: int) -> str:
    return COUNTRY_MAP.get(code, str(code))

def _normalize_val(val: Any) -> str:
    try:
        f = float(val)
        if f.is_integer(): return str(int(f))
        return str(f)
    except: return str(val).strip()

def _build_value_labels(survey: str, variable: str) -> Dict[str, str]:
    if not variable: return {}
    def fetch(srv=None):
        wc = "LOWER(variable) = LOWER(?)"
        p = [variable]
        if srv:
            wc += " AND survey = ?"
            p.append(srv)
        try:
            # Query from memory table
            rows = _con.execute(f"SELECT value, value_label FROM response_labels WHERE {wc}", p).fetchall()
            return {_normalize_val(r[0]): r[1] for r in rows}
        except: return {}
    m = fetch(survey)
    return m if m else fetch(None)

def _is_ecs(survey: str) -> bool:
    return survey in _ecs_surveys or survey.upper().startswith("ECSR")

# ---------------------------------------------------------------------
# Logic
# ---------------------------------------------------------------------

def list_surveys() -> List[Tuple[str, str]]:
    try:
        rows = _con.execute("SELECT DISTINCT Survey FROM dashboard_labels ORDER BY 1").fetchall()
        return [(r[0], r[0]) for r in rows]
    except: return []

def list_questions_for_survey(survey: str) -> List[Dict[str, Any]]:
    try:
        rows = _con.execute("SELECT Variable, \"Short\", Question FROM dashboard_labels WHERE Survey = ? ORDER BY Variable", [survey]).fetchall()
        return [{"id": r[0], "label": r[1], "description": r[2]} for r in rows if r[0] and r[0].lower() in _data_variables]
    except: return []

def list_longitudinal_questions() -> List[Dict[str, Any]]:
    try:
        rows = _con.execute("SELECT Variable, MIN(\"Short\"), MIN(Question), COUNT(DISTINCT Survey) as c FROM dashboard_labels GROUP BY Variable HAVING c > 1 ORDER BY 2").fetchall()
        return [{"id": r[0], "label": r[1], "description": r[2]} for r in rows if r[0] and r[0].lower() in _data_variables]
    except: return []

def list_weights_for_survey(survey: str) -> List[str]:
    if _is_ecs(survey):
        return ["emp_wei", "est_wei"]
    try:
        cols = _cols_main_map.values()
        return sorted([c for c in cols if c.lower().startswith('w') and c != 'wave'])
    except: return []

def get_survey_categories(survey: str) -> List[str]:
    is_ecs = _is_ecs(survey)
    table_cols = _cols_ecs_lower if is_ecs else _cols_main_lower
    candidates = ECS_CATEGORIES if is_ecs else EWCS_CATEGORIES
    valid = []
    for cat in candidates:
        if cat.lower() in table_cols:
            valid.append(cat)
    return valid

def weighted_pct(
    survey: str,
    question: str,
    weight: str,
    max_countries: int = 9999,
    min_pct: float = 0.0,
    category_group: Optional[str] = None,
    category_value: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    
    # 1. Setup
    is_ecs = _is_ecs(survey)
    table = "ecs_data" if is_ecs else "main_data"
    cols_lower = _cols_ecs_lower if is_ecs else _cols_main_lower
    cols_map = _cols_ecs_map if is_ecs else _cols_main_map
    
    # Resolve Label
    act_var, q_desc, orig_q = None, question, None
    try:
        r = _con.execute("SELECT Variable, Question, \"Question Number\" FROM dashboard_labels WHERE Survey = ? AND Variable = ? LIMIT 1", [survey, question]).fetchone()
        if not r: r = _con.execute("SELECT Variable, Question, \"Question Number\" FROM dashboard_labels WHERE Survey = ? AND \"Short\" = ? LIMIT 1", [survey, question]).fetchone()
        if r: act_var, q_desc, orig_q = r[0], r[1], str(r[2]) if r[2] else None
        else: act_var = question
    except: act_var = question

    # 2. Build Query
    df = pd.DataFrame()
    cat_sql = ""
    cat_p = []
    if category_group and category_value:
        if category_group.lower() in cols_lower:
            cat_sql = f" AND CAST({category_group} AS INTEGER) = ?"
            cat_p = [int(category_value)]

    cntry_col = cols_map.get('country', 'country')

    survey_candidates = [survey]
    if survey in SURVEY_YEARS:
        y = SURVEY_YEARS[survey]
        survey_candidates.append(str(y))
        survey_candidates.append(y)

    for s_cand in survey_candidates:
        try:
            # Strategy A: Wide
            if act_var.lower() in cols_lower:
                col_name = cols_map[act_var.lower()]
                sql = f"""
                    SELECT "{cntry_col}" AS country, CAST("{col_name}" AS FLOAT) as val, SUM({weight}) as w_sum, COUNT(*) as count
                    FROM {table} 
                    WHERE survey = ? AND "{col_name}" IS NOT NULL AND "{cntry_col}" IS NOT NULL {cat_sql}
                    GROUP BY 1, 2
                """
                df = _con.execute(sql, [s_cand] + cat_p).fetchdf()

            # Strategy B: Long
            elif 'question' in cols_lower and 'value' in cols_lower:
                sql = f"""
                    SELECT "{cntry_col}" AS country, CAST(value AS FLOAT) as val, SUM({weight}) as w_sum, COUNT(*) as count
                    FROM {table} 
                    WHERE survey = ? AND LOWER(question) = LOWER(?) AND value IS NOT NULL AND "{cntry_col}" IS NOT NULL {cat_sql}
                    GROUP BY 1, 2
                """
                df = _con.execute(sql, [s_cand, act_var] + cat_p).fetchdf()
            
            if not df.empty: break
        except Exception: pass

    if df.empty: return [], q_desc

    # 3. Labels
    val_map = _build_value_labels(survey, act_var)
    if not val_map and orig_q:
        val_map = _build_value_labels(survey, orig_q)
        if not val_map: val_map = _build_value_labels(survey, f"q{orig_q}")
        if not val_map: val_map = _build_value_labels(survey, f"Q{orig_q}")

    # Exclude non-response
    excl = ["dk", "dont know", "don't know", "na", "prefer not", "refusal", "no answer"]
    bad_vals = {v for v, l in val_map.items() if any(x in str(l).lower() for x in excl)}
    if bad_vals:
        df = df[~df["val"].apply(lambda x: _normalize_val(x) in bad_vals)]
        if df.empty: return [], q_desc

    # Calc
    df["w_total"] = df.groupby("country")["w_sum"].transform("sum")
    df["pct"] = (df["w_sum"] / df["w_total"]) * 100.0
    df["total_count"] = df.groupby("country")["count"].transform("sum")
    
    if min_pct: df = df[df["pct"] >= min_pct]
    
    df["country_label"] = df["country"].apply(lambda x: _map_country_label(survey, int(x)))
    df["value_label"] = df["val"].apply(lambda x: val_map.get(_normalize_val(x), str(x)))
    
    # SORTING: By val (numeric)
    df = df.sort_values(["country_label", "val"])
    
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "country": int(r["country"]),
            "country_label": r["country_label"],
            "value": str(r["val"]),
            "value_label": r["value_label"],
            "pct": float(r["pct"]),
            "count": int(r["count"]),
            "total_count": int(r["total_count"])
        })
    return rows, q_desc

def get_trend_data(q_short, weight, resps, cntrys=None, cat_grp=None, cat_val=None):
    var = q_short
    try:
        r = _con.execute("SELECT Variable FROM dashboard_labels WHERE \"Short\" = ? LIMIT 1", [q_short]).fetchone()
        if r: var = r[0]
    except: pass
    
    surveys = []
    try:
        r = _con.execute("SELECT DISTINCT Survey FROM dashboard_labels WHERE Variable = ? ORDER BY 1", [var]).fetchall()
        surveys = [x[0] for x in r]
    except: pass
    
    out = []
    for s in surveys:
        try:
            rows, _ = weighted_pct(s, var, weight, category_group=cat_grp, category_value=cat_val)
        except: continue
        if not rows: continue
        
        agg = {}
        for r in rows:
            c = r["country_label"]
            if cntrys and c not in cntrys: continue
            if c not in agg: agg[c] = {"v":0.0, "c":0, "tc":0}
            
            if r["value_label"] in resps:
                agg[c]["v"] += r["pct"]
                agg[c]["c"] += r["count"]
            if r["total_count"] > agg[c]["tc"]: agg[c]["tc"] = r["total_count"]
            
        for c, d in agg.items():
            if d["tc"] > 0:
                out.append({
                    "survey": s,
                    "year": SURVEY_YEARS.get(s, s),
                    "country": c,
                    "value": d["v"],
                    "count": d["c"],
                    "total_count": d["tc"]
                })
    return out
