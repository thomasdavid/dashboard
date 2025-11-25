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
    LABELS_FILE,
    RESPONSE_META_FILE,
    COUNTRY_FILE,
)

# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

SURVEY_YEARS = {
    "EWCSR1": 1991,
    "EWCSR2": 1995,
    "EWCSR3": 2000,
    "EWCSR4": 2005,
    "EWCSR5": 2010,
    "EWCSR6": 2015,
    "EWCS2021": 2021,
    "COVID": 2020
}

# ---------------------------------------------------------------------
# Initialise DuckDB
# ---------------------------------------------------------------------

print(f"--- Loading DuckDB with data from: {DATA_FILE}")
_con = duckdb.connect(database=":memory:")

# 1. Load Main Data (Handle Single vs Split Files)
if os.path.exists(DATA_FILE):
    print(f"--- Found single data file: {DATA_FILE}")
    load_path = str(DATA_FILE)
else:
    # Production/Render environment with split files
    load_path = str(DATA_FILE).replace("data.parquet", "split_*.parquet")
    print(f"--- Single file not found. Trying split pattern: {load_path}")

try:
    _con.execute(f"""
        CREATE OR REPLACE VIEW main_data AS
        SELECT * FROM read_parquet('{load_path}');
    """)
except Exception as e:
    print(f"!!! CRITICAL ERROR loading data: {e}")
    # Prevent crash on load, create dummy table
    _con.execute("CREATE OR REPLACE VIEW main_data AS SELECT 1 as dummy")

# --- CACHE AVAILABLE VARIABLES FOR FILTERING ---
print("--- DEBUG: Caching available data variables...")
_data_variables = set()

try:
    _cols_info = _con.execute("PRAGMA table_info(main_data)").fetchall()
    _cols_main_lower = {row[1].lower() for row in _cols_info}
    _cols_map = {row[1].lower(): row[1] for row in _cols_info}

    # Check if Long or Wide format
    if 'question' in _cols_main_lower and 'value' in _cols_main_lower:
        print("--- DEBUG: Dataset detected as LONG format. Scanning distinct questions...")
        # Fetch all unique values from the 'question' column
        rows = _con.execute("SELECT DISTINCT question FROM main_data").fetchall()
        _data_variables = {str(r[0]).lower() for r in rows}
    else:
        print("--- DEBUG: Dataset detected as WIDE format.")
        _data_variables = _cols_main_lower

    print(f"--- DEBUG: Found {len(_data_variables)} active variables in the dataset.")

except Exception as e:
    print(f"!!! Error inspecting data variables: {e}")
    _cols_main_lower = set()
    _cols_map = {}
# ------------------------------------------------

# 2. Load Labels (Question Metadata)
print(f"--- Loading Labels from: {LABELS_FILE}")
try:
    _con.execute(f"""
        CREATE OR REPLACE VIEW dashboard_labels AS
        SELECT 
            TRIM(Survey) AS Survey,
            "Question Number",
            Variable,
            Question,
            "Short"
        FROM read_csv('{LABELS_FILE}', auto_detect=True, header=True);
    """)
except Exception as e:
    print(f"!!! Error loading Labels.csv: {e}")

# 3. Load Response Labels
print(f"--- Loading Response Labels from: {RESPONSE_META_FILE}")
try:
    _con.execute(f"""
        CREATE OR REPLACE VIEW response_labels AS
        SELECT * FROM read_parquet('{RESPONSE_META_FILE}');
    """)
except Exception as e:
    print(f"!!! Error loading response_labels: {e}")

# ---------------------------------------------------------------------
# Country Map Loading
# ---------------------------------------------------------------------

COUNTRY_MAP = {}
try:
    _cmap_df = pd.read_csv(COUNTRY_FILE)
    _cmap_df.columns = _cmap_df.columns.str.strip().str.lower()
    if 'value' in _cmap_df.columns and 'label' in _cmap_df.columns:
        for _, row in _cmap_df.iterrows():
            try:
                COUNTRY_MAP[int(row["value"])] = row["label"]
            except ValueError:
                continue
except Exception as e:
    print(f"Warning: Could not load country map: {e}")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _map_country_label(survey: str, code: int) -> str:
    return COUNTRY_MAP.get(code, str(code))

def _normalize_val(val: Any) -> str:
    try:
        f_val = float(val)
        if f_val.is_integer():
            return str(int(f_val))
        return str(f_val)
    except (ValueError, TypeError):
        return str(val).strip()

def _build_value_labels(survey: str, variable: str) -> Dict[str, str]:
    if not variable:
        return {}
    
    def fetch(srv=None):
        where_clause = "LOWER(variable) = LOWER(?)"
        params = [variable]
        if srv:
            where_clause += " AND survey = ?"
            params.append(srv)
            
        sql = f"SELECT value, value_label FROM response_labels WHERE {where_clause}"
        try:
            rows = _con.execute(sql, params).fetchall()
            m = {}
            for r in rows:
                m[_normalize_val(r[0])] = r[1]
            return m
        except:
            return {}

    mapping = fetch(survey)
    if not mapping:
        mapping = fetch(None) 
        
    return mapping


# ---------------------------------------------------------------------
# Data Accessors
# ---------------------------------------------------------------------

def list_surveys() -> List[Tuple[str, str]]:
    try:
        q = "SELECT DISTINCT Survey FROM dashboard_labels ORDER BY 1"
        rows = _con.execute(q).fetchall()
        return [(r[0], r[0]) for r in rows]
    except Exception:
        return []

def list_longitudinal_questions() -> List[Dict[str, Any]]:
    """
    Returns questions that appear in more than one survey AND exist in the data.
    """
    q = """
        SELECT 
            Variable, 
            MIN("Short") as Label, 
            MIN(Question) as Desc,
            COUNT(DISTINCT Survey) as cnt
        FROM dashboard_labels
        GROUP BY Variable
        HAVING cnt > 1
        ORDER BY Label
    """
    try:
        rows = _con.execute(q).fetchall()
        valid_questions = []
        for r in rows:
            var_code = r[0]
            if var_code and var_code.lower() in _data_variables:
                valid_questions.append({
                    "id": var_code,
                    "label": r[1],
                    "description": r[2]
                })
        return valid_questions
    except Exception as e:
        print(f"Error listing longitudinal questions: {e}")
        return []

def list_questions_for_survey(survey: str) -> List[Dict[str, Any]]:
    q = """
        SELECT Variable, "Short", Question 
        FROM dashboard_labels
        WHERE Survey = ?
        ORDER BY Variable
    """
    try:
        rows = _con.execute(q, [survey]).fetchall()
        valid_questions = []
        for r in rows:
            var_code = r[0]
            if var_code and var_code.lower() in _data_variables:
                valid_questions.append({
                    "id": var_code,
                    "label": r[1],
                    "description": r[2]
                })
        return valid_questions
    except Exception:
        return []

def list_weights_for_survey(survey: str) -> List[str]:
    all_cols = sorted([val for key, val in _cols_map.items()])
    return sorted([c for c in all_cols if c.lower().startswith('w') and c != 'wave'])

def weighted_pct(
    survey: str,
    question: str,
    weight: str,
    max_countries: int = 9999,
    min_pct: float = 0.0,
    category_group: Optional[str] = None,
    category_value: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    
    # 1. Resolve Labels
    actual_variable = None
    original_question_num = None
    q_label_text = question

    try:
        # Try matching Variable first (Robust)
        lookup_var_sql = """
            SELECT Variable, Question, "Question Number"
            FROM dashboard_labels 
            WHERE Survey = ? AND Variable = ? 
            LIMIT 1
        """
        res = _con.execute(lookup_var_sql, [survey, question]).fetchone()
        
        if res:
            actual_variable = res[0]
            q_label_text = res[1]
            original_question_num = str(res[2]) if res[2] else None
        else:
            # Try matching "Short" label (Legacy support)
            lookup_short_sql = """
                SELECT Variable, Question, "Question Number"
                FROM dashboard_labels 
                WHERE Survey = ? AND "Short" = ? 
                LIMIT 1
            """
            res = _con.execute(lookup_short_sql, [survey, question]).fetchone()
            if res:
                actual_variable = res[0]
                q_label_text = res[1]
                original_question_num = str(res[2]) if res[2] else None
            else:
                actual_variable = question

    except Exception:
        actual_variable = question

    # 2. Query Data
    df = pd.DataFrame()
    
    # Build Category Filter SQL
    cat_filter_sql = ""
    cat_params = []
    
    if category_group and category_value:
        if category_group.lower() in _cols_main_lower:
            # CAST to INTEGER handles float values (1.0) in the database
            cat_filter_sql = f" AND CAST({category_group} AS INTEGER) = ?"
            cat_params = [int(category_value)]
        else:
            print(f"Warning: Category column {category_group} not found.")

    # Wide Format Query
    if actual_variable.lower() in _cols_main_lower:
        col_name = _cols_map[actual_variable.lower()]
        sql = f"""
            SELECT 
                country, 
                "{col_name}" as val, 
                SUM({weight}) as w_sum,
                COUNT(*) as count
            FROM main_data
            WHERE survey = ? AND "{col_name}" IS NOT NULL
            {cat_filter_sql}
            GROUP BY 1, 2
        """
        try:
            df = _con.execute(sql, [survey] + cat_params).fetchdf()
        except Exception as e:
            print(f"Query failed: {e}")
            pass 

    # Long Format Query
    elif 'question' in _cols_main_lower and 'value' in _cols_main_lower:
        sql = f"""
            SELECT 
                country, 
                value as val, 
                SUM({weight}) as w_sum,
                COUNT(*) as count
            FROM main_data
            WHERE survey = ? AND question = ? AND value IS NOT NULL
            {cat_filter_sql}
            GROUP BY 1, 2
        """
        try:
            df = _con.execute(sql, [survey, actual_variable] + cat_params).fetchdf()
        except Exception as e:
            print(f"Query failed: {e}")
            pass

    if df.empty:
        return [], q_label_text

    # Fetch Labels & Filter Exclusions
    val_map = {}
    if actual_variable:
        val_map = _build_value_labels(survey, actual_variable)
    
    if not val_map and original_question_num:
        val_map = _build_value_labels(survey, original_question_num)
        if not val_map:
             val_map = _build_value_labels(survey, f"q{original_question_num}")
             if not val_map:
                 val_map = _build_value_labels(survey, f"Q{original_question_num}")

    EXCLUSION_TERMS = ["dk", "dont know", "don't know", "na", "prefer not", "refusal", "no answer"]
    excluded_values = set()
    for val, label in val_map.items():
        if any(term in str(label).lower() for term in EXCLUSION_TERMS):
            excluded_values.add(val)
    
    if excluded_values:
        def is_excluded(row_val):
            return _normalize_val(row_val) in excluded_values
        df = df[~df["val"].apply(is_excluded)]

    if df.empty:
        return [], q_label_text

    # Calculate Totals and Percentages
    df["w_total"] = df.groupby("country")["w_sum"].transform("sum")
    df["pct"] = (df["w_sum"] / df["w_total"]) * 100.0
    
    df["total_count"] = df.groupby("country")["count"].transform("sum")
    
    if min_pct > 0:
        df = df[df["pct"] >= min_pct]

    df["country_label"] = df["country"].apply(lambda x: _map_country_label(survey, x))
    
    def get_val_label(val):
        norm = _normalize_val(val)
        return val_map.get(norm, str(val))

    df["value_label"] = df["val"].apply(get_val_label)
    df = df.sort_values(["country_label", "val"])
    
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "country": int(row["country"]),
            "country_label": row["country_label"],
            "value": str(row["val"]),
            "value_label": row["value_label"],
            "pct": float(row["pct"]),
            "count": int(row["count"]),
            "total_count": int(row["total_count"])
        })
        
    return rows, q_label_text

def get_trend_data(
    question_short: str,
    weight: str,
    response_labels: List[str],
    countries: Optional[List[str]] = None,
    category_group: Optional[str] = None,
    category_value: Optional[str] = None,
) -> List[Dict[str, Any]]:
    
    # Resolve variable from short label (or use as is)
    var_code = question_short
    try:
        res = _con.execute("""
            SELECT Variable FROM dashboard_labels 
            WHERE "Short" = ? LIMIT 1
        """, [question_short]).fetchone()
        if res:
            var_code = res[0]
    except:
        pass

    # Find surveys having this variable (filtered by actual data existence)
    q = """
        SELECT DISTINCT Survey 
        FROM dashboard_labels 
        WHERE Variable = ?
        ORDER BY Survey
    """
    surveys = [r[0] for r in _con.execute(q, [var_code]).fetchall()]
    
    results = []
    
    for survey in surveys:
        year = SURVEY_YEARS.get(survey, survey)
        
        try:
            rows, _ = weighted_pct(
                survey, var_code, weight, 
                category_group=category_group, 
                category_value=category_value
            )
        except:
            continue 
            
        if not rows:
            continue
            
        country_aggs = {}
        for row in rows:
            c_label = row["country_label"]
            
            if countries and c_label not in countries:
                continue
                
            if c_label not in country_aggs:
                country_aggs[c_label] = {
                    "value": 0.0, 
                    "count": 0, 
                    "total_count": 0
                }
            
            if row["value_label"] in response_labels:
                country_aggs[c_label]["value"] += row["pct"]
                country_aggs[c_label]["count"] += row["count"]
                
            if row["total_count"] > country_aggs[c_label]["total_count"]:
                country_aggs[c_label]["total_count"] = row["total_count"]
        
        for c_label, agg in country_aggs.items():
            if agg["total
