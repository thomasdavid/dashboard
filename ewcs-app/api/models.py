from pydantic import BaseModel, Field
from typing import List, Optional

class WeightedRequest(BaseModel):
    survey: str = Field(..., description="e.g., EWCSR2")
    question: str = Field(..., description="e.g., q2a")
    weight_set: str = Field("w5", description="'none','w4','w5'")
    global_mult: float = 1.0
    countries: Optional[List[str]] = None  # optional filter

class WeightedRow(BaseModel):
    country: str
    value: str
    pct: float

class WeightedResponse(BaseModel):
    survey: str
    question: str
    weight_set: str
    rows: List[WeightedRow]
