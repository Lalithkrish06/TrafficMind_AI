"""
Traffic Congestion Prediction AI v2
- Real CSV import support
- New features: road_type, lane_count, accidents_nearby
- SQLite database with extended schema
"""

import os
import numpy as np
import pandas as pd
import sqlite3
import pickle
import json
import random
from datetime import datetime, timedelta

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import (
    accuracy_score, classification_report,
    f1_score, precision_score, recall_score
)
from sklearn.pipeline import Pipeline

os.makedirs("models", exist_ok=True)
os.makedirs("data",   exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
#  UPDATED FEATURES  (includes 3 new fields)
# ─────────────────────────────────────────────────────────────────────────────
FEATURES = [
    "hour", "day_of_week", "month",
    "temperature", "rainfall",
    "vehicle_count", "avg_speed", "road_capacity",
    "is_holiday", "is_weekend", "special_event",
    # ── NEW ──────────────────────────────
    "road_type_enc",   # encoded: highway=0, arterial=1, local=2, expressway=3
    "lane_count",      # number of lanes (1–6)
    "accidents_nearby",# count of accidents within 2 km in last 1 hour
]

ROAD_TYPES   = ["highway", "arterial", "local", "expressway"]
ROAD_TYPE_ENC = {r: i for i, r in enumerate(ROAD_TYPES)}

# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────────────────────
def init_database(db_path="traffic_ai.db"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS traffic_data (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT,
            location         TEXT,
            hour             INTEGER,
            day_of_week      INTEGER,
            month            INTEGER,
            temperature      REAL,
            rainfall         REAL,
            vehicle_count    INTEGER,
            avg_speed        REAL,
            road_capacity    INTEGER,
            is_holiday       INTEGER,
            is_weekend       INTEGER,
            special_event    INTEGER,
            road_type        TEXT,
            road_type_enc    INTEGER,
            lane_count       INTEGER,
            accidents_nearby INTEGER,
            congestion_level TEXT,
            congestion_score REAL,
            data_source      TEXT DEFAULT 'synthetic'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS model_metrics (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name    TEXT,
            accuracy      REAL,
            precision_val REAL,
            recall_val    REAL,
            f1_val        REAL,
            cv_mean       REAL,
            trained_at    TEXT,
            dataset_size  INTEGER,
            feature_count INTEGER,
            notes         TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            predicted_at     TEXT,
            location         TEXT,
            hour             INTEGER,
            day_of_week      INTEGER,
            temperature      REAL,
            rainfall         REAL,
            vehicle_count    INTEGER,
            avg_speed        REAL,
            road_capacity    INTEGER,
            road_type        TEXT,
            lane_count       INTEGER,
            accidents_nearby INTEGER,
            predicted_level  TEXT,
            confidence       REAL,
            actual_level     TEXT
        )
    """)

    conn.commit()
    print("[DB] Database initialised → traffic_ai.db")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
#  1. REAL CSV IMPORT
# ─────────────────────────────────────────────────────────────────────────────
REQUIRED_COLS = {
    "hour", "day_of_week", "month", "temperature", "rainfall",
    "vehicle_count", "avg_speed", "road_capacity",
    "is_holiday", "is_weekend", "special_event", "congestion_level"
}

def load_from_csv(csv_path: str) -> pd.DataFrame:
    """
    Load real traffic data from a CSV file.

    Minimum required columns:
        hour, day_of_week, month, temperature, rainfall,
        vehicle_count, avg_speed, road_capacity,
        is_holiday, is_weekend, special_event, congestion_level

    Optional (will be generated if missing):
        road_type, lane_count, accidents_nearby, timestamp, location
    """
    print(f"[CSV] Loading data from: {csv_path}")
    df = pd.read_csv(csv_path)
    print(f"[CSV] Loaded {len(df)} rows, columns: {list(df.columns)}")

    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    # ── Fill optional columns if absent ──────────────────────────────────────
    if "road_type" not in df.columns:
        df["road_type"] = np.random.choice(ROAD_TYPES, len(df))
        print("[CSV]  road_type  not found → random-filled")

    if "lane_count" not in df.columns:
        df["lane_count"] = np.random.randint(1, 7, len(df))
        print("[CSV]  lane_count not found → random-filled (1–6)")

    if "accidents_nearby" not in df.columns:
        df["accidents_nearby"] = np.random.poisson(0.5, len(df))
        print("[CSV]  accidents_nearby not found → Poisson-filled")

    if "timestamp" not in df.columns:
        df["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if "location" not in df.columns:
        df["location"] = "Unknown"

    df["road_type_enc"] = df["road_type"].map(ROAD_TYPE_ENC).fillna(1).astype(int)
    df["congestion_level"] = df["congestion_level"].str.strip().str.title()
    df["data_source"] = "csv"

    valid_levels = {"Low", "Medium", "High", "Critical"}
    invalid = set(df["congestion_level"].unique()) - valid_levels
    if invalid:
        print(f"[CSV]  Warning: unknown levels {invalid} → will be dropped")
        df = df[df["congestion_level"].isin(valid_levels)]

    print(f"[CSV] Clean rows: {len(df)}")
    print(f"[CSV] Level distribution:\n{df['congestion_level'].value_counts().to_string()}\n")
    return df


def generate_sample_csv(path="data/sample_traffic.csv", n=500):
    """Generate a ready-to-use CSV so users can see the expected format."""
    df = generate_traffic_data(n)
    df.to_csv(path, index=False)
    print(f"[CSV] Sample CSV saved → {path}")


# ─────────────────────────────────────────────────────────────────────────────
#  SYNTHETIC DATA GENERATION  (now includes new features)
# ─────────────────────────────────────────────────────────────────────────────
def generate_traffic_data(n_samples=5000):
    print(f"[DATA] Generating {n_samples} synthetic samples …")
    random.seed(42); np.random.seed(42)

    locations = [
        "Main Street","Highway NH-45","City Center","Ring Road",
        "Market Junction","Airport Road","School Zone","IT Park",
        "Bus Stand","Railway Station",
    ]
    road_type_map = {
        "Highway NH-45": "highway", "Airport Road": "highway",
        "Ring Road": "arterial",    "City Center":  "arterial",
        "Main Street": "arterial",  "Market Junction": "local",
        "School Zone": "local",     "Bus Stand": "local",
        "IT Park": "expressway",    "Railway Station": "arterial",
    }
    lane_map = {
        "highway": (4,6), "arterial": (2,4), "local": (1,2), "expressway": (4,6)
    }

    records = []
    base_time = datetime(2023, 1, 1)

    for _ in range(n_samples):
        ts          = base_time + timedelta(hours=random.randint(0, 8760))
        hour        = ts.hour
        dow         = ts.weekday()
        month       = ts.month
        location    = random.choice(locations)
        road_type   = road_type_map.get(location, "arterial")
        ln_min, ln_max = lane_map[road_type]
        lane_count  = random.randint(ln_min, ln_max)
        is_weekend  = 1 if dow >= 5 else 0
        is_holiday  = 1 if random.random() < 0.05 else 0
        special_ev  = 1 if random.random() < 0.08 else 0

        peak_morning = 1 if 7 <= hour <= 9  else 0
        peak_evening = 1 if 17 <= hour <= 19 else 0

        temp     = 25 + 10*np.sin(month*np.pi/6) + np.random.normal(0, 3)
        rainfall = max(0, np.random.exponential(2) if month in [6,7,8,9]
                       else np.random.exponential(0.5))

        base_vehicles = 200
        if peak_morning or peak_evening: base_vehicles += 400
        if is_weekend:                   base_vehicles -= 100
        if special_ev or is_holiday:     base_vehicles += 300

        # More lanes → more capacity but also more vehicles
        capacity_mult = lane_count * 250
        road_capacity = capacity_mult
        vehicle_count = max(50, int(base_vehicles + np.random.normal(0, 60)))

        occupancy = vehicle_count / road_capacity

        base_speed = 60
        if road_type == "highway":     base_speed = 80
        elif road_type == "expressway":base_speed = 100
        elif road_type == "local":     base_speed = 40

        if occupancy > 0.8:  base_speed *= 0.2
        elif occupancy > 0.6:base_speed *= 0.45
        elif occupancy > 0.4:base_speed *= 0.70
        if rainfall > 5:     base_speed *= 0.85
        avg_speed = max(5, base_speed + np.random.normal(0, 5))

        # Accidents: Poisson, higher during rain/rush
        accident_rate = 0.3
        if rainfall > 5:      accident_rate += 0.4
        if peak_morning or peak_evening: accident_rate += 0.3
        accidents_nearby = int(np.random.poisson(accident_rate))

        # Congestion score incorporates accidents
        score = (occupancy*0.45
                 + (1 - avg_speed/120)*0.25
                 + (peak_morning+peak_evening)*0.1
                 + rainfall/20*0.08
                 + accidents_nearby*0.06
                 + (1/max(lane_count,1))*0.06)
        score = max(0, min(1, score))

        if score < 0.25:   level = "Low"
        elif score < 0.50: level = "Medium"
        elif score < 0.75: level = "High"
        else:              level = "Critical"

        records.append({
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "location": location, "hour": hour, "day_of_week": dow, "month": month,
            "temperature": round(temp,1), "rainfall": round(rainfall,2),
            "vehicle_count": vehicle_count, "avg_speed": round(avg_speed,1),
            "road_capacity": road_capacity,
            "is_holiday": is_holiday, "is_weekend": is_weekend, "special_event": special_ev,
            "road_type": road_type, "road_type_enc": ROAD_TYPE_ENC[road_type],
            "lane_count": lane_count, "accidents_nearby": accidents_nearby,
            "congestion_level": level, "congestion_score": round(score,3),
            "data_source": "synthetic",
        })

    df = pd.DataFrame(records)
    print(f"[DATA] Distribution:\n{df['congestion_level'].value_counts().to_string()}\n")
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  TRAINING
# ─────────────────────────────────────────────────────────────────────────────
def train_models(df, conn):
    X = df[FEATURES]
    y = df["congestion_level"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y)

    models = {
        "Random Forest": RandomForestClassifier(
            n_estimators=150, max_depth=12, random_state=42, n_jobs=-1),
        "Gradient Boosting": GradientBoostingClassifier(
            n_estimators=100, learning_rate=0.1, max_depth=5, random_state=42),
        "Logistic Regression": LogisticRegression(max_iter=1000, random_state=42),
    }

    results, best_acc, best_model, best_name = {}, 0, None, ""

    for name, clf in models.items():
        print(f"\n[TRAIN] {name} …")
        pipeline = (Pipeline([("scaler", StandardScaler()), ("clf", clf)])
                    if name == "Logistic Regression"
                    else Pipeline([("clf", clf)]))

        pipeline.fit(X_train, y_train)
        y_pred = pipeline.predict(X_test)

        acc  = accuracy_score(y_test, y_pred)
        prec = precision_score(y_test, y_pred, average="weighted", zero_division=0)
        rec  = recall_score(y_test,  y_pred, average="weighted", zero_division=0)
        f1   = f1_score(y_test,      y_pred, average="weighted", zero_division=0)
        cv   = cross_val_score(pipeline, X_train, y_train, cv=5).mean()

        print(f"  Accuracy: {acc:.4f} ({acc*100:.2f}%)")
        print(f"  F1:       {f1:.4f}  Precision: {prec:.4f}  Recall: {rec:.4f}")
        print(f"  CV Mean:  {cv:.4f}")
        print(classification_report(y_test, y_pred))

        conn.execute("""
            INSERT INTO model_metrics
              (model_name,accuracy,precision_val,recall_val,f1_val,cv_mean,
               trained_at,dataset_size,feature_count,notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (name, acc, prec, rec, f1, cv,
              datetime.now().isoformat(), len(df), len(FEATURES),
              f"features={','.join(FEATURES)}"))
        conn.commit()

        results[name] = dict(accuracy=round(acc,4), precision=round(prec,4),
                             recall=round(rec,4), f1=round(f1,4),
                             cv_mean=round(cv,4), pipeline=pipeline)
        if acc > best_acc:
            best_acc, best_model, best_name = acc, pipeline, name

    print(f"\n[BEST] {best_name} → {best_acc*100:.2f}%")
    return best_model, best_name, results, X_test, y_test


def save_artifacts(model, model_name, results, df):
    with open("models/best_model.pkl", "wb") as f:
        pickle.dump(model, f)
    meta = {
        "best_model":   model_name,
        "features":     FEATURES,
        "classes":      ["Low","Medium","High","Critical"],
        "trained_at":   datetime.now().isoformat(),
        "dataset_size": len(df),
        "all_results":  {k:{kk:vv for kk,vv in v.items() if kk!="pipeline"}
                         for k,v in results.items()},
    }
    with open("models/model_metadata.json", "w") as f:
        json.dump(meta, f, indent=2)
    print("[SAVE] models/best_model.pkl + model_metadata.json written")


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("="*60)
    print("  TRAFFIC CONGESTION PREDICTION AI v2 — TRAINING")
    print("="*60)

    conn = init_database()

    # ── Choose data source ─────────────────────────────────────────────────
    CSV_PATH = "data/my_traffic_data.csv"   # ← put your real CSV here

    if os.path.exists(CSV_PATH):
        print(f"\n[DATA] Real CSV found → {CSV_PATH}")
        df = load_from_csv(CSV_PATH)
    else:
        print(f"\n[DATA] No real CSV at {CSV_PATH} → using synthetic data")
        print("[DATA] Tip: generate a sample CSV template with:")
        print("       generate_sample_csv('data/sample_traffic.csv')\n")
        df = generate_traffic_data(5000)

    df.to_sql("traffic_data", conn, if_exists="replace", index=False)
    print(f"[DB] {len(df)} rows saved to traffic_data table")

    best_model, best_name, results, X_test, y_test = train_models(df, conn)
    save_artifacts(best_model, best_name, results, df)

    conn.close()
    print("\n[DONE] Training complete! Run: python api.py")
