from pydantic import BaseModel
from typing import List


class Survey(BaseModel):
    id: str
    label: str


class Question(BaseModel):
    variable: str
    question_label: str


class Weight(BaseModel):
    id: str
    label: str


class WeightedRow(BaseModel):
    country: int
    country_label: str
    value: str
    value_label: str
    pct: float


class WeightedResponse(BaseModel):
    survey: str
    question: str
    question_label: str
    rows: List[WeightedRow]
