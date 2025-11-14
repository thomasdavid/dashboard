import os
from dotenv import load_dotenv
load_dotenv()

DATA_DIR = os.getenv("DATA_DIR", "/app/data")         # where Parquet lives
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")   # set to your site in prod

# Optional: map survey -> file path (override by env)
SURVEY_MAP = {
    "EWCSR1": os.path.join(DATA_DIR, "EWCSR1.parquet"),
    "EWCSR2": os.path.join(DATA_DIR, "EWCSR2.parquet"),
    "EWCSR3": os.path.join(DATA_DIR, "EWCSR3.parquet"),
    "EWCSR4": os.path.join(DATA_DIR, "EWCSR4.parquet"),
}
