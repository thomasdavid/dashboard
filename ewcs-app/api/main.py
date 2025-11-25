from __future__ import annotations

from typing import List, Optional, Any
import os
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .settings import ALLOWED_ORIGINS
from .service_duckdb import (
    list_surveys,
    list_questions_for_survey,
    list_longitudinal_questions,
    list_weights_for_survey,
    weighted_pct,
    get_trend_data
)

app = FastAPI(title="EWCS prototype API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Models ---
class Survey(BaseModel):
    id: str
    label: str

class QuestionOut(BaseModel):
    id: str
    label: str
    description: Optional[str] = None

class Weight(BaseModel):
    id: str
    label: str

class WeightedRow(BaseModel):
    country: int
    country_label: str
    value: str
    value_label: str
    pct: float
    count: int
    total_count: int

class WeightedResponse(BaseModel):
    question_label: str
    rows: List[WeightedRow]

class TrendPoint(BaseModel):
    survey: str
    year: Any
    country: str
    value: float
    count: int
    total_count: int

# --- Endpoints ---

@app.get("/")
async def read_index():
    file_path = os.path.join(os.path.dirname(__file__), "../web/index1.html")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Index file not found")
    return FileResponse(file_path)

@app.get("/surveys", response_model=List[Survey])
def get_surveys():
    try:
        surveys = list_surveys()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [Survey(id=s, label=label) for (s, label) in surveys]

@app.get("/questions/longitudinal", response_model=List[QuestionOut])
def get_long_questions():
    try:
        rows = list_longitudinal_questions()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [QuestionOut(**r) for r in rows]

@app.get("/questions/{survey}", response_model=List[QuestionOut])
def get_questions(survey: str):
    try:
        rows = list_questions_for_survey(survey)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [QuestionOut(**r) for r in rows]

@app.get("/weights/{survey}", response_model=List[Weight])
def get_weights(survey: str):
    try:
        weights = list_weights_for_survey(survey)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [Weight(id=w, label=w) for w in weights]

@app.get("/weighted", response_model=WeightedResponse)
def get_weighted(
    survey: str,
    question: str,
    weight: str,
    max_countries: int = 9999,
    min_pct: float = 0.0,
    # NEW PARAMS
    category_group: Optional[str] = None,
    category_value: Optional[str] = None,
):
    try:
        rows, q_label = weighted_pct(
            survey, question, weight, 
            max_countries, min_pct, 
            category_group, category_value
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return WeightedResponse(question_label=q_label, rows=rows)

@app.get("/trend", response_model=List[TrendPoint])
def get_trend(
    question: str,
    weight: str,
    responses: List[str] = Query(...),
    countries: Optional[List[str]] = Query(None),
    # NEW PARAMS
    category_group: Optional[str] = Query(None),
    category_value: Optional[str] = Query(None),
):
    try:
        data = get_trend_data(
            question, weight, responses, countries,
            category_group, category_value
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return data
