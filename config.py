# config.py
import os
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    # --- Database ---
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'dream.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Flask-WTF CSRF / Sessions ---
    SECRET_KEY = "dev"  # change later

    # --- Assessment constants ---
    # Max raw scores (adjust if your tests change)
    ARITH_MAX = 38
    REASON_MAX = 35

    READING_P1_MAX = 40
    READING_P2_MAX = 40
    SPAG_SPELLING_MAX = 40
    SPAG_GRAMMAR_MAX = 40

    # Band thresholds (%)
    WTS_MAX = 55.0  # < 55 → Working towards ARE
    OT_MAX = 75.0   # 55–75 → Working at ARE
    # >= 75 → Exceeding ARE

    BAND_THRESHOLDS = {
        "maths": {"wts_max": WTS_MAX, "ot_max": OT_MAX},
        "reading": {"wts_max": 65, "ot_max": 85},
        "spag": {"wts_max": 65, "ot_max": 85},
    }
