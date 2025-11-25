from pathlib import Path
import os

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))

# Data Files
DATA_FILE = DATA_DIR / "data.parquet"       # EWCS
ECS_DATA_FILE = DATA_DIR / "ECSdata.parquet" # ECS (New)

# Metadata
LABELS_FILE = DATA_DIR / "Labels.csv"
RESPONSE_META_FILE = DATA_DIR / "response_labels.parquet"
COUNTRY_FILE = DATA_DIR / "country.csv"

ALLOWED_ORIGINS = ["*"]
