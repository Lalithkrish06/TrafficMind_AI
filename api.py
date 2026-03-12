"""
Traffic Congestion Prediction AI v2 — FastAPI REST Server
Run:  python api.py
Docs: http://localhost:8000/docs
"""

import os, json, pickle, sqlite3
from datetime import datetime
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────────────────────
MODEL_PATH = "models/best_model.pkl"
META_PATH  = "models/model_metadata.json"
DB_PATH    = "traffic_ai.db"

FEATURES = [
    "hour", "day_of_week", "month",
    "temperature", "rainfall",
    "vehicle_count", "avg_speed", "road_capacity",
    "is_holiday", "is_weekend", "special_event",
    "road_type_enc", "lane_count", "accidents_nearby",
]

ROAD_TYPE_ENC = {"highway": 0, "arterial": 1, "local": 2, "expressway": 3}

ADVICE = {
    "Low":      "Roads are clear. Normal travel time expected.",
    "Medium":   "Moderate traffic. Allow 10–15 extra minutes.",
    "High":     "Heavy traffic. Consider alternate routes.",
    "Critical": "Severe congestion! Avoid this route if possible.",
}

# ─────────────────────────────────────────────────────────────────────────────
#  LOAD MODEL
# ─────────────────────────────────────────────────────────────────────────────
def load_model():
    if not os.path.exists(MODEL_PATH):
        raise RuntimeError("Model not found. Run train_model.py first.")
    with open(MODEL_PATH, "rb") as f:
        model = pickle.load(f)
    with open(META_PATH, "r") as f:
        meta = json.load(f)
    return model, meta

try:
    model, meta = load_model()
    print(f"[API] Model loaded: {meta['best_model']}")
    best_metrics = meta["all_results"][meta["best_model"]]
    print(f"[API] Accuracy: {best_metrics['accuracy']*100:.2f}%")
except Exception as e:
    model, meta = None, {}
    print(f"[API] Warning: {e}")

# ─────────────────────────────────────────────────────────────────────────────
#  FASTAPI APP
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="TrafficMind AI API",
    description="Predict traffic congestion level using AI",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve dashboard at root
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

def get_db():
    return sqlite3.connect(DB_PATH)

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────
class PredictRequest(BaseModel):
    location:         str   = Field("Main Street", example="City Center")
    hour:             int   = Field(..., ge=0, le=23,  example=8)
    day_of_week:      int   = Field(..., ge=0, le=6,   example=1)
    month:            int   = Field(..., ge=1, le=12,  example=6)
    temperature:      float = Field(..., example=30.0)
    rainfall:         float = Field(0.0,  ge=0,        example=0.0)
    vehicle_count:    int   = Field(..., ge=0,         example=500)
    avg_speed:        float = Field(..., ge=0,         example=25.0)
    road_capacity:    int   = Field(..., ge=1,         example=800)
    is_holiday:       int   = Field(0,   ge=0, le=1,   example=0)
    is_weekend:       int   = Field(0,   ge=0, le=1,   example=0)
    special_event:    int   = Field(0,   ge=0, le=1,   example=0)
    road_type:        str   = Field("arterial", example="highway")
    lane_count:       int   = Field(2,   ge=1, le=8,   example=4)
    accidents_nearby: int   = Field(0,   ge=0,         example=1)

class PredictResponse(BaseModel):
    location:         str
    predicted_level:  str
    confidence:       float
    probabilities:    dict
    advice:           str
    features_used:    dict
    predicted_at:     str

class BatchRequest(BaseModel):
    records: list[PredictRequest]

# ─────────────────────────────────────────────────────────────────────────────
#  PREDICTION HELPER
# ─────────────────────────────────────────────────────────────────────────────
def run_prediction(req: PredictRequest) -> dict:
    if model is None:
        raise HTTPException(503, "Model not loaded. Run train_model.py first.")

    road_enc = ROAD_TYPE_ENC.get(req.road_type.lower(), 1)
    feat_vals = [
        req.hour, req.day_of_week, req.month,
        req.temperature, req.rainfall,
        req.vehicle_count, req.avg_speed, req.road_capacity,
        req.is_holiday, req.is_weekend, req.special_event,
        road_enc, req.lane_count, req.accidents_nearby,
    ]
    X = np.array([feat_vals])
    label   = model.predict(X)[0]
    proba   = model.predict_proba(X)[0]
    classes = model.classes_
    conf    = round(float(max(proba)) * 100, 2)
    proba_d = {c: round(float(p)*100, 2) for c, p in zip(classes, proba)}

    return dict(
        location=req.location,
        predicted_level=label,
        confidence=conf,
        probabilities=proba_d,
        advice=ADVICE[label],
        features_used={f: v for f, v in zip(FEATURES, feat_vals)},
        predicted_at=datetime.now().isoformat(),
    )

def save_prediction_to_db(req: PredictRequest, result: dict):
    conn = get_db()
    conn.execute("""
        INSERT INTO predictions
          (predicted_at,location,hour,day_of_week,temperature,rainfall,
           vehicle_count,avg_speed,road_capacity,road_type,lane_count,
           accidents_nearby,predicted_level,confidence,actual_level)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (result["predicted_at"], req.location, req.hour, req.day_of_week,
          req.temperature, req.rainfall, req.vehicle_count, req.avg_speed,
          req.road_capacity, req.road_type, req.lane_count, req.accidents_nearby,
          result["predicted_level"], result["confidence"], None))
    conn.commit()
    conn.close()

# ─────────────────────────────────────────────────────────────────────────────
#  ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    return {"message": "TrafficMind AI API v2", "docs": "/docs", "status": "ok"}


@app.get("/health", tags=["Info"])
def health():
    return {
        "status":       "ok",
        "model_loaded": model is not None,
        "model_name":   meta.get("best_model", "N/A"),
        "timestamp":    datetime.now().isoformat(),
    }


@app.get("/metrics", tags=["Model"])
def get_metrics():
    """Return accuracy, F1, precision, recall for all trained models."""
    if not meta:
        raise HTTPException(503, "Metadata not loaded.")
    return {
        "best_model":    meta["best_model"],
        "dataset_size":  meta["dataset_size"],
        "features":      meta["features"],
        "trained_at":    meta["trained_at"],
        "all_results":   meta["all_results"],
    }


@app.post("/predict", response_model=PredictResponse, tags=["Predict"])
def predict(req: PredictRequest):
    """Single prediction with confidence + advice."""
    result = run_prediction(req)
    save_prediction_to_db(req, result)
    return result


@app.post("/predict/batch", tags=["Predict"])
def predict_batch(batch: BatchRequest):
    """Batch prediction for multiple records."""
    results = []
    for req in batch.records:
        r = run_prediction(req)
        save_prediction_to_db(req, r)
        results.append(r)
    return {"count": len(results), "predictions": results}


@app.get("/history", tags=["Database"])
def get_history(limit: int = 20, location: Optional[str] = None):
    """Fetch recent predictions from the database."""
    conn = get_db()
    if location:
        rows = conn.execute(
            "SELECT * FROM predictions WHERE location=? ORDER BY id DESC LIMIT ?",
            (location, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM predictions ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    cols = [d[0] for d in conn.execute(
        "PRAGMA table_info(predictions)").fetchall()]
    conn.close()
    return {"count": len(rows), "records": [dict(zip(cols, r)) for r in rows]}


@app.get("/stats", tags=["Database"])
def get_stats():
    """Summary statistics from the predictions table."""
    conn = get_db()
    total  = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    by_lvl = conn.execute(
        "SELECT predicted_level, COUNT(*) FROM predictions GROUP BY predicted_level"
    ).fetchall()
    by_loc = conn.execute(
        "SELECT location, COUNT(*) FROM predictions GROUP BY location ORDER BY 2 DESC LIMIT 5"
    ).fetchall()
    conn.close()
    return {
        "total_predictions": total,
        "by_level":    {r[0]: r[1] for r in by_lvl},
        "top_locations": {r[0]: r[1] for r in by_loc},
    }


@app.get("/live", tags=["Live"])
def live_snapshot():
    """
    Live-update endpoint — returns a fresh simulated snapshot.
    The dashboard polls this every 5 seconds.
    """
    import random, math
    now    = datetime.now()
    hour   = now.hour
    peak   = 1 if (7 <= hour <= 9) or (17 <= hour <= 19) else 0
    base   = 350 + peak*300 + random.randint(-60, 60)
    speed  = max(8, 55 - peak*35 + random.randint(-10, 10))
    rain   = max(0, random.gauss(0, 1) if random.random() > 0.8 else 0)
    score  = (base/800)*0.5 + (1-speed/80)*0.3 + peak*0.1 + rain/20*0.1
    score  = max(0, min(1, score))

    if score < 0.25:   level = "Low"
    elif score < 0.50: level = "Medium"
    elif score < 0.75: level = "High"
    else:              level = "Critical"

    locations = [
        ("Main Street",   random.randint(200, 700)),
        ("City Center",   random.randint(300, 900)),
        ("Highway NH-45", random.randint(100, 500)),
        ("Ring Road",     random.randint(150, 600)),
        ("Airport Road",  random.randint(50,  400)),
    ]

    return {
        "timestamp":   now.isoformat(),
        "overall_level": level,
        "overall_score": round(score, 3),
        "peak_hour": bool(peak),
        "locations": [
            {
                "name":    name,
                "vehicles": vc,
                "speed":   max(8, 60 - int(vc/20) + random.randint(-5,5)),
                "level":   ("Critical" if vc>750 else "High" if vc>500
                            else "Medium" if vc>300 else "Low"),
            }
            for name, vc in locations
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*50)
    print("  TrafficMind AI API v2")
    print("  http://localhost:8000")
    print("  http://localhost:8000/docs  ← Swagger UI")
    print("="*50 + "\n")
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
