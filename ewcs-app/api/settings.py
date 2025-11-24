from pathlib import Path
import os

# Base directory of the project (…/dashboard)
BASE_DIR = Path(__file__).resolve().parent.parent

# Directory with the parquet/CSV data files
# In Render you set DATA_DIR=/var/data as env var
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))

# Main long-format data
DATA_FILE = DATA_DIR / "data.parquet"

# --- NEW: Labels file for Short/Long questions ---
LABELS_FILE = DATA_DIR / "Labels.csv"

# Response / value labels
RESPONSE_META_FILE = DATA_DIR / "response_labels.parquet"

# Country mapping (Survey, Code, Value, Label)
COUNTRY_FILE = DATA_DIR / "country.csv"

# CORS origins – adjust as needed
ALLOWED_ORIGINS = [
    "*",  # for local dev; you can remove this in production
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5500",
]