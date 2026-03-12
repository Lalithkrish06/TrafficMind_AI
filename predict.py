"""
Traffic Congestion Prediction AI - Prediction Engine
Usage: python predict.py
"""

import pickle
import json
import sqlite3
import numpy as np
from datetime import datetime

MODEL_PATH    = "models/best_model.pkl"
META_PATH     = "models/model_metadata.json"
DB_PATH       = "traffic_ai.db"

FEATURES = [
    "hour", "day_of_week", "month", "temperature", "rainfall",
    "vehicle_count", "avg_speed", "road_capacity",
    "is_holiday", "is_weekend", "special_event",
]

LEVEL_COLORS = {
    "Low":      "🟢",
    "Medium":   "🟡",
    "High":     "🟠",
    "Critical": "🔴",
}

LEVEL_ADVICE = {
    "Low":      "Roads are clear. Normal travel time expected.",
    "Medium":   "Moderate traffic. Allow 10–15 extra minutes.",
    "High":     "Heavy traffic. Consider alternate routes.",
    "Critical": "Severe congestion! Avoid this route if possible.",
}


def load_model():
    with open(MODEL_PATH, "rb") as f:
        model = pickle.load(f)
    with open(META_PATH, "r") as f:
        meta = json.load(f)
    return model, meta


def predict(model, features: dict) -> dict:
    X = np.array([[features[f] for f in FEATURES]])
    label  = model.predict(X)[0]
    proba  = model.predict_proba(X)[0]
    classes = model.classes_
    conf   = float(max(proba))
    proba_dict = {c: round(float(p), 4) for c, p in zip(classes, proba)}
    return {
        "predicted_level": label,
        "confidence":      round(conf * 100, 2),
        "probabilities":   proba_dict,
        "advice":          LEVEL_ADVICE[label],
        "icon":            LEVEL_COLORS[label],
    }


def save_prediction(conn, features, result, location):
    conn.execute("""
        INSERT INTO predictions
          (predicted_at, location, hour, day_of_week, temperature,
           vehicle_count, predicted_level, confidence, actual_level)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(),
        location,
        features["hour"],
        features["day_of_week"],
        features["temperature"],
        features["vehicle_count"],
        result["predicted_level"],
        result["confidence"],
        None,
    ))
    conn.commit()


def interactive_predict():
    print("\n" + "=" * 50)
    print("  TRAFFIC CONGESTION PREDICTOR")
    print("=" * 50)

    try:
        model, meta = load_model()
        print(f"✅ Model loaded: {meta['best_model']}")
        best = meta["all_results"][meta["best_model"]]
        print(f"   Accuracy: {best['accuracy']*100:.2f}%  |  F1: {best['f1']:.4f}")
    except FileNotFoundError:
        print("❌ Model not found. Run train_model.py first.")
        return

    conn = sqlite3.connect(DB_PATH)

    print("\nEnter traffic details (press Enter for defaults):\n")

    def ask(prompt, default, cast=float):
        val = input(f"  {prompt} [{default}]: ").strip()
        return cast(val) if val else default

    location     = input("  Location name [Main Street]: ").strip() or "Main Street"
    hour         = int(ask("Hour (0-23)", datetime.now().hour, int))
    day_of_week  = int(ask("Day of week (0=Mon … 6=Sun)", datetime.now().weekday(), int))
    month        = int(ask("Month (1-12)", datetime.now().month, int))
    temperature  = ask("Temperature (°C)", 28.0)
    rainfall     = ask("Rainfall (mm)", 0.0)
    vehicle_count= int(ask("Vehicle count", 350, int))
    avg_speed    = ask("Avg speed (km/h)", 45.0)
    road_capacity= int(ask("Road capacity", 800, int))
    is_holiday   = int(ask("Is holiday? (0/1)", 0, int))
    is_weekend   = int(ask("Is weekend? (0/1)", 1 if day_of_week >= 5 else 0, int))
    special_event= int(ask("Special event? (0/1)", 0, int))

    features = {
        "hour": hour, "day_of_week": day_of_week, "month": month,
        "temperature": temperature, "rainfall": rainfall,
        "vehicle_count": vehicle_count, "avg_speed": avg_speed,
        "road_capacity": road_capacity, "is_holiday": is_holiday,
        "is_weekend": is_weekend, "special_event": special_event,
    }

    result = predict(model, features)
    save_prediction(conn, features, result, location)

    print("\n" + "─" * 50)
    print(f"  {result['icon']}  PREDICTION: {result['predicted_level'].upper()} CONGESTION")
    print(f"  📊 Confidence: {result['confidence']}%")
    print(f"  📍 Location: {location}")
    print(f"  💡 {result['advice']}")
    print("\n  Probability breakdown:")
    for lvl, p in sorted(result["probabilities"].items(), key=lambda x: -x[1]):
        bar = "█" * int(p * 30)
        print(f"    {LEVEL_COLORS[lvl]} {lvl:<10} {p*100:5.1f}%  {bar}")
    print("─" * 50)

    conn.close()


if __name__ == "__main__":
    interactive_predict()
