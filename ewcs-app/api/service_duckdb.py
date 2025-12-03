"""
EWCS Dashboard – DuckDB + Pandas backend (service_duckdb.py)
"""
from __future__ import annotations

import duckdb
import pandas as pd
from typing import Dict, List, Tuple, Any, Optional
import traceback
import os
import tempfile

from .settings import (
    DATA_FILE, ECS_DATA_FILE, LABELS_FILE, RESPONSE_META_FILE, COUNTRY_FILE, DATA_DIR 
)

# Define path for new survey if not in settings
EWCS24_DATA_FILE = DATA_DIR / "EWCSR8.parquet"

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

SURVEY_YEARS = {
    "EWCSR1": 1991, "EWCSR2": 1995, "EWCSR3": 2000, "EWCSR4": 2005,
    "EWCSR5": 2010, "EWCSR6": 2015, "EWCS2021": 2021, "COVID": 2020,
    "ECSR1": 2004, "ECSR2": 2009, "ECSR3": 2013, "ECSR4": 2019,
    "EWCSR8": 2024
}

EU27_CODES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28}

ECS_CATEGORIES = ['sector13', 'mm102grp', 'sector2', 'rev_type', 'sec3', 'size_5', 'size_10']
EWCS_CATEGORIES = ['agesex', 'isco']

# UPDATED: Swapped 'bdwn_ISCO_1' for 'ISCO_1'
EWCS24_CATEGORIES = ['sex2', 'age3', 'bdwn_NACE0_lbl', 'ISCO_1', 'bdwn_wstatus', 'part_time']

# ---------------------------------------------------------------------
# Initialise DuckDB
# ---------------------------------------------------------------------
print(f"--- Initialising DuckDB...")
db_path = os.path.join(tempfile.gettempdir(), "ewcs_buffer.duckdb")
_con = duckdb.connect(database=db_path)

try:
    _con.execute("PRAGMA memory_limit='256MB'") 
    _con.execute("PRAGMA threads=2")
    _con.execute("PRAGMA temp_directory='/tmp'")
except Exception as e:
    print(f"!!! Warning: Could not set memory limits: {e}")

def load_view(name, path):
    if os.path.exists(path):
        print(f"--- Linking {name}: {path}")
        try:
            _con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}');")
        except:
            _con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT 1 as dummy")
    else:
        _con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT 1 as dummy")

ewcs_path = str(DATA_FILE).replace("data.parquet", "split_*.parquet") if not os.path.exists(DATA_FILE) else str(DATA_FILE)
load_view("main_data", ewcs_path)
load_view("ecs_data", ECS_DATA_FILE)
load_view("ewcs24_data", EWCS24_DATA_FILE)

# Load Metadata Tables
try:
    _con.execute(f"CREATE OR REPLACE TABLE dashboard_labels AS SELECT TRIM(Survey) AS Survey, \"Question Number\", Variable, Question, \"Short\" FROM read_csv('{LABELS_FILE}', auto_detect=True, header=True);")
except: pass

try:
    resp_csv = str(RESPONSE_META_FILE).replace(".parquet", ".csv")
    if os.path.exists(resp_csv):
        _con.execute(f"CREATE OR REPLACE TABLE response_labels AS SELECT * FROM read_csv('{resp_csv}', auto_detect=True, header=True);")
    else:
        _con.execute(f"CREATE OR REPLACE TABLE response_labels AS SELECT * FROM read_parquet('{RESPONSE_META_FILE}');")
    _con.execute("CREATE INDEX idx_resp_survey_var ON response_labels(survey, variable)")
except: pass

# ---------------------------------------------------------------------
# Caching & Mapping
# ---------------------------------------------------------------------
_data_variables = set()
_cols_map_generic = {} 
_cols_ewcs24_lower = set()
_cols_ecs_lower = set()
_cols_main_lower = set()

def _cache_columns(table_name, target_set):
    try:
        r = _con.execute(f"PRAGMA table_info({table_name})").fetchall()
        cols = {x[1].lower() for x in r}
        target_set.update(cols)
        for x in r: _cols_map_generic[x[1].lower()] = x[1]
        if 'question' in cols:
            count = _con.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0]
            if count > 1:
                q_rows = _con.execute(f"SELECT DISTINCT question FROM {table_name} WHERE question IS NOT NULL").fetchall()
                _data_variables.update({str(row[0]).lower() for row in q_rows})
        else:
            _data_variables.update(cols)
    except: pass

_cache_columns("main_data", _cols_main_lower)
_cache_columns("ecs_data", _cols_ecs_lower)
_cache_columns("ewcs24_data", _cols_ewcs24_lower)

COUNTRY_MAP = {}
GLOBAL_COUNTRY_MAP = {}

try:
    cdf = pd.read_csv(COUNTRY_FILE)
    cdf.columns = cdf.columns.str.strip().str.lower()
    for _, row in cdf.iterrows():
        try:
            val_id = int(row["value"])
            lbl = row["label"]
            GLOBAL_COUNTRY_MAP[val_id] = lbl
            if 'survey' in cdf.columns and pd.notna(row['survey']):
                COUNTRY_MAP[(str(row['survey']).strip(), val_id)] = lbl
        except: continue
except: pass

try:
    sql = "SELECT survey, value, value_label FROM response_labels WHERE LOWER(variable) IN ('country', 'cntry', 'country_iso', 'y11', 'country_code')"
    rows = _con.execute(sql).fetchall()
    for r in rows:
        try:
            COUNTRY_MAP[(str(r[0]).strip(), int(float(r[1])))] = r[2]
            if int(float(r[1])) not in GLOBAL_COUNTRY_MAP: GLOBAL_COUNTRY_MAP[int(float(r[1]))] = r[2]
        except: continue
except: pass

# Auto-Inject EWCSR8 Metadata
try:
    chk = _con.execute("SELECT COUNT(*) FROM dashboard_labels WHERE Survey = 'EWCSR8'").fetchone()
    has_data_24 = False
    try:
        _con.execute("SELECT 1 FROM ewcs24_data LIMIT 1")
        has_data_24 = True
    except: pass

    if chk and chk[0] == 0 and has_data_24:
        print("--- DEBUG: EWCSR8 missing from labels. Auto-detecting...")
        tbl_info = _con.execute("PRAGMA table_info(ewcs24_data)").fetchall()
        cols_lower = {c[1].lower() for c in tbl_info}
        generated_vars = []
        if 'question' in cols_lower:
            q_rows = _con.execute("SELECT DISTINCT question FROM ewcs24_data WHERE question IS NOT NULL").fetchall()
            generated_vars = [str(r[0]) for r in q_rows]
        else:
            exclude_cols = {'country', 'calweight', 'weight', 'w', 'survey', 'year', 'int_length', 'eu27', 'eu28', 'is_eu', 'hhold_id', 'p_id', 'id'}
            for col in tbl_info:
                if col[1].lower() not in exclude_cols: generated_vars.append(col[1])

        for var_code in generated_vars:
            q_text = f"{var_code} (Auto-detected)" 
            _con.execute("INSERT INTO dashboard_labels VALUES (?, ?, ?, ?, ?)", ['EWCSR8', var_code, var_code, q_text, var_code])
except: pass

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _map_country_label(survey: str, code: int) -> str:
    if code == 999: return "EU27"
    if (survey, code) in COUNTRY_MAP: return COUNTRY_MAP[(survey, code)]
    if code in GLOBAL_COUNTRY_MAP: return GLOBAL_COUNTRY_MAP[code]
    return str(code)

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

def _is_ewcs24(survey: str) -> bool: return survey == "EWCSR8"
def _is_ecs(survey: str) -> bool: return survey in _ecs_surveys or survey.upper().startswith("ECSR")

def get_category_values(survey: str, category_col: str) -> List[int]:
    tbl = "ewcs24_data" if _is_ewcs24(survey) else ("ecs_data" if _is_ecs(survey) else "main_data")
    try:
        rows = _con.execute(f"SELECT DISTINCT {category_col} FROM {tbl} WHERE {category_col} IS NOT NULL ORDER BY 1").fetchall()
        return [r[0] for r in rows]
    except: return []

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
    if _is_ewcs24(survey): return ["calweight"]
    if _is_ecs(survey): return ["emp_wei", "est_wei"]
    try:
        cols = _cols_map_generic.values()
        return sorted([c for c in cols if c.lower().startswith('w') and c != 'wave'])
    except: return []

def get_survey_categories(survey: str) -> List[str]:
    candidates = EWCS24_CATEGORIES if _is_ewcs24(survey) else (ECS_CATEGORIES if _is_ecs(survey) else EWCS_CATEGORIES)
    table_cols = _cols_ewcs24_lower if _is_ewcs24(survey) else (_cols_ecs_lower if _is_ecs(survey) else _cols_main_lower)
    return [c for c in candidates if c.lower() in table_cols]

def weighted_pct(
    survey: str, question: str, weight: str, max_countries: int = 9999, min_pct: float = 0.0,
    category_group: Optional[str] = None, category_value: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    
    # 1. Setup
    if _is_ewcs24(survey):
        table, cols_lower = "ewcs24_data", _cols_ewcs24_lower
    elif _is_ecs(survey):
        table, cols_lower = "ecs_data", _cols_ecs_lower
    else:
        table, cols_lower = "main_data", _cols_main_lower
    
    act_var, q_desc, orig_q = None, question, None
    try:
        r = _con.execute("SELECT Variable, Question, \"Question Number\" FROM dashboard_labels WHERE Survey = ? AND Variable = ? LIMIT 1", [survey, question]).fetchone()
        if not r: r = _con.execute("SELECT Variable, Question, \"Question Number\" FROM dashboard_labels WHERE Survey = ? AND \"Short\" = ? LIMIT 1", [survey, question]).fetchone()
        if r: act_var, q_desc, orig_q = r[0], r[1], str(r[2]) if r[2] else None
        else: act_var = question
    except: act_var = question

    # 2. Filter Logic (Smart Match)
    df = pd.DataFrame()
    cat_sql = ""
    cat_p = []
    
    if category_group and category_value:
        if category_group.lower() in cols_lower:
            is_numeric_filter = False
            int_val = 0
            try:
                f_val = float(category_value)
                if f_val.is_integer():
                    is_numeric_filter = True
                    int_val = int(f_val)
            except: pass

            if is_numeric_filter:
                cat_sql = f" AND (TRY_CAST({category_group} AS INTEGER) = ? OR CAST({category_group} AS VARCHAR) LIKE ? || '.%')"
                cat_p = [int_val, str(int_val)]
            else:
                cat_sql = f" AND CAST({category_group} AS VARCHAR) = ?"
                cat_p = [str(category_value)]

    cntry_col = "country"
    if "country" not in cols_lower:
        for k, v in _cols_map_generic.items():
            if k == "country": cntry_col = v; break

    survey_candidates = [survey]
    if survey in SURVEY_YEARS:
        y = SURVEY_YEARS[survey]; survey_candidates.extend([str(y), y])

    for s_cand in survey_candidates:
        try:
            if 'question' in cols_lower and 'value' in cols_lower:
                sql = f"SELECT \"{cntry_col}\" AS country, CAST(value AS FLOAT) as val, {weight} as w FROM {table} WHERE survey = ? AND LOWER(question) = LOWER(?) AND value IS NOT NULL AND \"{cntry_col}\" IS NOT NULL {cat_sql}"
                raw_df = _con.execute(sql, [s_cand, act_var] + cat_p).fetchdf()
            elif act_var.lower() in cols_lower:
                col_name = act_var
                for k, v in _cols_map_generic.items():
                    if k == act_var.lower(): col_name = v; break
                sql = f"SELECT \"{cntry_col}\" AS country, CAST(\"{col_name}\" AS FLOAT) as val, {weight} as w FROM {table} WHERE survey = ? AND \"{col_name}\" IS NOT NULL AND \"{cntry_col}\" IS NOT NULL {cat_sql}"
                raw_df = _con.execute(sql, [s_cand] + cat_p).fetchdf()
            if not raw_df.empty: df = raw_df; break
        except: pass

    if df.empty: return [], q_desc

    val_map = _build_value_labels(survey, act_var)
    if not val_map and orig_q:
        val_map = _build_value_labels(survey, orig_q)
        if not val_map: val_map = _build_value_labels(survey, f"q{orig_q}")
    
    excl = ["dk", "dont know", "don't know", "na", "prefer not", "refusal", "no answer"]
    bad_vals = {v for v, l in val_map.items() if any(x in str(l).lower() for x in excl)}
    if bad_vals: df = df[~df["val"].apply(lambda x: _normalize_val(x) in bad_vals)]
    if df.empty: return [], q_desc

    # 4. Aggregation
    def aggregate_chunk(d, c_code):
        g = d.groupby("val")
        res = g["w"].sum().reset_index(); res.rename(columns={"w": "w_sum"}, inplace=True)
        res["count"] = g["w"].count().values 
        total_w = res["w_sum"].sum(); total_c = res["count"].sum()
        if total_w == 0: return pd.DataFrame()
        res["pct"] = (res["w_sum"] / total_w) * 100.0
        res["pct"] = res["pct"].round(1) 
        res["country"] = c_code; res["total_count"] = total_c
        return res

    final_rows = []
    for c_code, c_df in df.groupby("country"):
        agg = aggregate_chunk(c_df, int(c_code))
        if not agg.empty: final_rows.append(agg)

    eu27_df = df[df["country"].isin(EU27_CODES)]
    if not eu27_df.empty:
        eu_agg = aggregate_chunk(eu27_df, 999) 
        if not eu_agg.empty: final_rows.append(eu_agg)

    if not final_rows: return [], q_desc
    result_df = pd.concat(final_rows, ignore_index=True)
    if min_pct: result_df = result_df[result_df["pct"] >= min_pct]
    
    result_df["country_label"] = result_df["country"].apply(lambda x: _map_country_label(survey, int(x)))
    result_df["value_label"] = result_df["val"].apply(lambda x: val_map.get(_normalize_val(x), str(x)))
    result_df = result_df.sort_values(["country_label", "val"])
    
    out_list = []
    for _, r in result_df.iterrows():
        out_list.append({
            "country": int(r["country"]),
            "country_label": r["country_label"],
            "value": str(r["val"]),
            "value_label": r["value_label"],
            "pct": float(r["pct"]),
            "count": int(r["count"]),
            "total_count": int(r["total_count"])
        })
    return out_list, q_desc

def get_trend_data(q_short, weight, resps, cntrys=None, cat_grp=None, cat_val=None):
    return []

def export_full_dataset(survey: str, weight: str = "calweight") -> pd.DataFrame:
    print(f"--- Starting Bulk Export for {survey}...")
    vars_list = list_questions_for_survey(survey)
    valid_vars = [v['id'] for v in vars_list]
    if not valid_vars: return pd.DataFrame()

    categories_to_process = [{"col": None, "val": 0, "name": "Total"}]
    survey_cats = get_survey_categories(survey)
    for cat_col in survey_cats:
        distinct_vals = get_category_values(survey, cat_col)
        for val in distinct_vals:
            categories_to_process.append({"col": cat_col, "val": val, "name": f"{cat_col}_{val}"})

    all_rows = []
    for idx, var_id in enumerate(valid_vars):
        if idx % 5 == 0: print(f"Exporting {idx}/{len(valid_vars)}: {var_id}")
        for cat_def in categories_to_process:
            cat_col, cat_val = cat_def['col'], cat_def['val']
            filter_id = str(cat_val) 
            try:
                if cat_col:
                    data, _ = weighted_pct(survey, var_id, weight, category_group=cat_col, category_value=str(cat_val))
                else:
                    data, _ = weighted_pct(survey, var_id, weight)
                if not data: continue
                for r in data:
                    country_code = r['country']
                    is_eu = (country_code == 999) or (country_code in EU27_CODES)
                    row_val = r['value'] 
                    csv_row = {
                        "Question_ID": var_id, "country": country_code, "Score": round(r['pct'], 1),
                        "unique_value": row_val, "FilterID": filter_id, "Survey": survey,
                        "LabelId": f"{survey}{var_id}", "EU": is_eu,
                        "Duplicateremove": f"{survey}{var_id}_{row_val}_{filter_id}_{country_code}",
                        "Value_key": f"{survey}{var_id}_{row_val}"
                    }
                    all_rows.append(csv_row)
            except: continue
    return pd.DataFrame(all_rows)
