from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .models import WeightedRequest, WeightedResponse, WeightedRow
from .settings import SURVEY_MAP, ALLOWED_ORIGINS
from .service_duckdb import weighted_pct

app = FastAPI(title="EWCS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGINS] if ALLOWED_ORIGINS != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/surveys")
def list_surveys():
    return {"surveys": sorted(SURVEY_MAP.keys())}

@app.get("/questions/{survey}")
def list_questions(survey: str):
    import duckdb
    path = SURVEY_MAP.get(survey)
    if not path:
        raise HTTPException(404, f"Unknown survey {survey}")
    q = duckdb.connect().execute(
        f"SELECT DISTINCT question FROM read_parquet('{path}') WHERE question IS NOT NULL ORDER BY 1"
    ).fetchall()
    return {"survey": survey, "questions": [r[0] for r in q]}

@app.post("/weighted", response_model=WeightedResponse)
def weighted(req: WeightedRequest):
    path = SURVEY_MAP.get(req.survey)
    if not path:
        raise HTTPException(404, f"Unknown survey {req.survey}")

    rows, _, _ = weighted_pct(
        path=path,
        question=req.question,
        weight_set=req.weight_set,
        global_mult=req.global_mult,
        countries=req.countries,
    )
    return WeightedResponse(
        survey=req.survey,
        question=req.question,
        weight_set=req.weight_set,
        rows=[WeightedRow(**r) for r in rows]
    )
