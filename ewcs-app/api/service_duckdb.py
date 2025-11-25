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

# Potential Category Columns in ECS
ECS_CATEGORIES = ['sector13', 'mm102grp', 'sector2', 'rev_type', 'sec3', 'size_5', 'size_10']
EWCS_CATEGORIES = ['agesex', 'isco']

# ---------------------------------------------------------------------
# Initialise DuckDB
# ---------------------------------------------------------------------

print(f"--- Loading DuckDB...")
_con = duckdb.connect(database=":memory:")

# 1. Load EWCS Data
if os.path.exists(DATA_FILE):
    print(f"--- Loading EWCS data: {DATA_FILE}")
    ewcs_path = str(DATA_FILE)
else:
    ewcs_path = str(DATA_FILE).replace("data.parquet", "split_*.parquet")
    print(f"--- Loading EWCS split: {ewcs_path}")

try:
    _con.execute(f"CREATE OR REPLACE VIEW main_data AS SELECT * FROM read_parquet('{ewcs_path}');")
except Exception as e:
    print(f"!!! Error loading EWCS data: {e}")
    _con.execute("CREATE OR REPLACE VIEW main_data AS SELECT 1 as dummy")

# 2. Load ECS Data
if os.path.exists(ECS_DATA_FILE):
    print(f"--- Loading ECS data: {ECS_DATA_FILE}")
    try:
        _con.execute(f"CREATE OR REPLACE VIEW ecs_data AS SELECT * FROM read_parquet('{ECS_DATA_FILE}');")
    except Exception as e:
        print(f"!!! Error loading ECS data: {e}")
        _con.execute("CREATE OR REPLACE VIEW ecs_data AS SELECT 1 as dummy")
else:
    print(f"!!! ECS Data file not found: {ECS_DATA_FILE}")
    _con.execute("CREATE OR REPLACE VIEW ecs_data AS SELECT 1 as dummy")

# --- CACHE METADATA ---
print("--- DEBUG: Caching metadata...")
_data_variables = set()
_ecs_surveys = set()

try:
    # EWCS Vars
    r = _con.execute("PRAGMA table_info(main_data)").fetchall()
    cols = {x[1].lower() for x in r}
    if 'question' in cols:
        rows = _con.execute("SELECT DISTINCT question FROM main_data").fetchall()
        _data_variables.update({str(row[0]).lower() for row in rows})
    else:
        _data_variables.update(cols)
        
    # ECS Vars & Surveys
    r_ecs = _con.execute("PRAGMA table_info(ecs_data)").fetchall()
    ecs_cols = {x[1].lower() for x in r_ecs}
    if 'survey' in ecs_cols:
        s_rows = _con.execute("SELECT DISTINCT survey FROM ecs_data").fetchall()
        _ecs_surveys = {str(row[0]) for row in s_rows}
        print(f"--- DEBUG: ECS Surveys found: {_ecs_surveys}")
        
        if 'question' in ecs_cols:
             q_rows = _con.execute("SELECT DISTINCT question FROM ecs_data").fetchall()
             _data_variables.update({str(row[0]).lower() for row in q_rows})

except Exception as e:
    print(f"!!! Metadata Cache Error: {e}")

# 3. Labels
try:
    _con.execute(f"CREATE OR REPLACE VIEW dashboard_labels AS SELECT TRIM(Survey) AS Survey, \"Question Number\", Variable, Question, \"Short\" FROM read_csv('{LABELS_FILE}', auto_detect=True, header=True);")
except: pass

try:
    _con.execute(f"CREATE OR REPLACE VIEW response_labels AS SELECT * FROM read_parquet('{RESPONSE_META_FILE}');")
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
            rows = _con.execute(f"SELECT value, value_label FROM response_labels WHERE {wc}", p).fetchall()
            return {_normalize_val(r[0]): r[1] for r in rows}
        except: return {}
    m = fetch(survey)
    return m if m else fetch(None)

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
    # Switch logic based on survey type
    if survey in _ecs_surveys:
        return ["emp_wei", "est_wei"]
    
    # EWCS Logic
    try:
        r = _con.execute("PRAGMA table_info(main_data)").fetchall()
        cols = [x[1] for x in r]
        return sorted([c for c in cols if c.lower().startswith('w') and c != 'wave'])
    except: return []

def get_survey_categories(survey: str) -> List[str]:
    """
    Checks which category columns have valid data for the given survey.
    """
    table = "ecs_data" if survey in _ecs_surveys else "main_data"
    candidates = ECS_CATEGORIES if survey in _ecs_surveys else EWCS_CATEGORIES
    
    valid = []
    try:
        # Check if table exists first
        _con.execute(f"SELECT 1 FROM {table} LIMIT 0")
        
        # Check columns exist in table
        t_info = _con.execute(f"PRAGMA table_info({table})").fetchall()
        t_cols = {x[1].lower() for x in t_info}
        
        for cat in candidates:
            if cat.lower() in t_cols:
                # Optional: Check if non-null data exists for this survey
                # This can be slow for large data, so we trust the column existence + survey filter
                # sql = f"SELECT 1 FROM {table} WHERE survey = ? AND {cat} IS NOT NULL LIMIT 1"
                # if _con.execute(sql, [survey]).fetchone():
                valid.append(cat)
    except:
        pass
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
    is_ecs = survey in _ecs_surveys
    table = "ecs_data" if is_ecs else "main_data"
    
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
    
    # Category Filter
    cat_sql = ""
    cat_p = []
    if category_group and category_value:
        try:
            # Check if cat col exists
            _con.execute(f"SELECT {category_group} FROM {table} LIMIT 0")
            cat_sql = f" AND CAST({category_group} AS INTEGER) = ?"
            cat_p = [int(category_value)]
        except: pass

    # Query
    try:
        # Check Wide
        _con.execute(f"SELECT \"{act_var}\" FROM {table} LIMIT 0")
        # Wide format query
        sql = f"""
            SELECT country, "{act_var}" as val, SUM({weight}) as w_sum, COUNT(*) as count
            FROM {table} WHERE survey = ? AND "{act_var}" IS NOT NULL {cat_sql}
            GROUP BY 1, 2
        """
        df = _con.execute(sql, [survey] + cat_p).fetchdf()
    except:
        # Try Long
        try:
            sql = f"""
                SELECT country, value as val, SUM({weight}) as w_sum, COUNT(*) as count
                FROM {table} WHERE survey = ? AND question = ? AND value IS NOT NULL {cat_sql}
                GROUP BY 1, 2
            """
            df = _con.execute(sql, [survey, act_var] + cat_p).fetchdf()
        except: pass

    if df.empty: return [], q_desc

    # 3. Labels
    val_map = _build_value_labels(survey, act_var)
    if not val_map and orig_q:
        val_map = _build_value_labels(survey, orig_q)
        if not val_map: val_map = _build_value_labels(survey, f"q{orig_q}")
    
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
    
    df["country_label"] = df["country"].apply(lambda x: _map_country_label(survey, x))
    df["value_label"] = df["val"].apply(lambda x: val_map.get(_normalize_val(x), str(x)))
    
    rows = []
    for _, r in df.sort_values(["country_label", "val"]).iterrows():
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
    # Resolve var
    var = q_short
    try:
        r = _con.execute("SELECT Variable FROM dashboard_labels WHERE \"Short\" = ? LIMIT 1", [q_short]).fetchone()
        if r: var = r[0]
    except: pass
    
    # Find surveys
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
