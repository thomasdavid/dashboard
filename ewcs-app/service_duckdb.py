import duckdb
from typing import Iterable, Tuple

# One process-wide connection (DuckDB is embedded)
_con = duckdb.connect()

def _in_list_sql(items: Iterable[str]) -> str:
    # Safe-ish literalization (for short lists)
    quoted = [ "'" + s.replace("'", "''") + "'" for s in items ]
    return "(" + ",".join(quoted) + ")" if quoted else "(NULL)"  # empty never matches

def weighted_pct(path: str, question: str,
                 weight_set: str, global_mult: float,
                 countries: Iterable[str] | None) -> Tuple[list, list, list]:
    # Choose weight expression
    if weight_set == "w4":
        wexpr = "coalesce(w4,0)"
    elif weight_set == "w5":
        wexpr = "coalesce(w5,0)"
    else:
        wexpr = "1.0"
    wexpr = f"({wexpr}) * {float(global_mult)}"

    where = "question = ?"
    params = [question]
    if countries:
        where += f" AND country IN {_in_list_sql(countries)}"

    sql = f"""
    WITH base AS (
      SELECT country, value, {wexpr} AS w
      FROM read_parquet('{path}')
      WHERE {where}
    ),
    agg AS (
      SELECT country, value, SUM(w) AS wsum
      FROM base GROUP BY country, value
    ),
    tot AS (
      SELECT country, SUM(wsum) AS wtot
      FROM agg GROUP BY country
    )
    SELECT a.country, a.value, 100.0 * a.wsum / NULLIF(t.wtot, 0) AS pct
    FROM agg a JOIN tot t USING (country)
    ORDER BY a.country, a.value
    """

    rows = _con.execute(sql, params).fetchall()
    # normalize
    out = []
    countries_set, values_set = set(), set()
    for c, v, p in rows:
        cs = "" if c is None else str(c)
        vs = "NA" if v is None else str(v)
        out.append({"country": cs, "value": vs, "pct": float(p or 0.0)})
        countries_set.add(cs); values_set.add(vs)

    countries_sorted = sorted(countries_set)
    values_sorted = sorted(values_set, key=lambda x: (x.isdigit(), x) if not x.isdigit() else (True, int(x)))
    return out, countries_sorted, values_sorted
