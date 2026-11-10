"""Task Complexity Experiment - Flask backend.

Replicates Payne (1976) Experiment 2 in a web environment.
- Main page: subject ID + condition selection
- Experiment page: matrix-cell accordion UI (once opened, stays open)
- Logs per-click events and per-session summary to CSV files under logs/
"""

import csv
import json
import os
import random
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request, redirect, url_for

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

SUMMARY_CSV = LOGS_DIR / "summary.csv"
SUMMARY_COLUMNS = [
    "subject_id", "condition_id", "n_alternatives", "n_dimensions",
    "participant_type", "agent_framework", "agent_model",
    "total_time_ms", "total_clicks", "unique_info_viewed", "pct_info_searched",
    "final_choice", "started_at", "completed_at",
]
EVENT_COLUMNS = [
    "timestamp", "subject_id", "condition_id", "event_type",
    "apartment_id", "dimension_id", "value", "elapsed_ms",
    "x", "y",
]

app = Flask(__name__)
_CSV_APPEND_LOCK = threading.RLock()


# ---------- Data loading ----------
def load_data():
    # utf-8-sig strips optional BOM (Excel-friendly CSVs)
    apartments = pd.read_csv(DATA_DIR / "apartments.csv", encoding="utf-8-sig")
    dimensions = pd.read_csv(DATA_DIR / "dimensions.csv", encoding="utf-8-sig")
    conditions = pd.read_csv(DATA_DIR / "conditions.csv", encoding="utf-8-sig")
    # Strip incidental whitespace in string cells (handles e.g. "$110 " from Excel edits)
    for df in (apartments, dimensions, conditions):
        for col in df.select_dtypes(include="object").columns:
            df[col] = df[col].str.strip()
    return apartments, dimensions, conditions


def get_condition(condition_id):
    _, _, conditions = load_data()
    row = conditions[conditions["condition_id"] == condition_id]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def get_experiment_payload(condition_id):
    apartments, dimensions, conditions = load_data()
    cond = conditions[conditions["condition_id"] == condition_id]
    if cond.empty:
        return None
    cond = cond.iloc[0].to_dict()
    n_alt = int(cond["n_alternatives"])
    n_dim = int(cond["n_dimensions"])
    apartments_sel = apartments.head(n_alt).to_dict(orient="records")
    dims_sel = dimensions[dimensions["min_condition_level"] <= n_dim] \
        .sort_values("dim_id") \
        .to_dict(orient="records")
    return {"condition": cond, "apartments": apartments_sel, "dimensions": dims_sel}


def get_reveal_value(condition_id, apartment_id, dimension_id):
    payload = get_experiment_payload(condition_id)
    if payload is None:
        return None, f"unknown condition: {condition_id!r}"

    valid_dimensions = {d["column_name"] for d in payload["dimensions"]}
    if dimension_id not in valid_dimensions:
        return None, f"dimension not in condition: {dimension_id!r}"

    for apt in payload["apartments"]:
        if str(apt.get("apartment_id")) == apartment_id:
            value = apt.get(dimension_id)
            if pd.isna(value):
                value = ""
            return str(value), None

    return None, f"apartment not in condition: {apartment_id!r}"


# ---------- Logging helpers ----------
def event_log_path(subject_id, condition_id, started_at_ms):
    safe_subj = "".join(c for c in subject_id if c.isalnum() or c in "-_")
    return LOGS_DIR / f"events_{safe_subj}_{condition_id}_{started_at_ms}.csv"


def _append_with_excel_friendly_bom(path: Path, columns, row: dict):
    """Serialize CSV appends within this Flask process.

    The browser can issue ``/log`` and ``/reveal`` concurrently for one click.
    Without a lock, two ``csv.DictWriter.writerow`` calls can interleave bytes
    and split one logical row into fragments.
    """
    with _CSV_APPEND_LOCK:
        _append_with_excel_friendly_bom_unlocked(path, columns, row)


def _append_with_excel_friendly_bom_unlocked(path: Path, columns, row: dict):
    """Append a row to a CSV. If the file doesn't exist yet, create it with a
    UTF-8 BOM so Excel (Windows) opens Korean text correctly. Subsequent
    appends use plain UTF-8 to avoid writing a BOM mid-file.

    Schema migration: if the existing file's header doesn't match `columns`,
    archive the old file (`.v{n}.bak`) and start fresh with the new schema.
    This handles additive changes like the agent metadata columns.
    """
    file_exists = path.exists()
    if file_exists:
        with open(path, "r", encoding="utf-8-sig") as f:
            first_line = f.readline().rstrip("\r\n")
        existing_cols = [c.strip() for c in first_line.split(",")] if first_line else []
        if existing_cols != columns:
            backup_path = path.with_suffix(path.suffix + f".{int(time.time())}.bak")
            path.rename(backup_path)
            print(f"[schema-migration] archived {path.name} -> {backup_path.name}")
            file_exists = False
    encoding = "utf-8-sig" if not file_exists else "utf-8"
    with open(path, "a", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in columns})


def append_event(path: Path, row: dict):
    _append_with_excel_friendly_bom(path, EVENT_COLUMNS, row)


def append_summary(row: dict):
    _append_with_excel_friendly_bom(SUMMARY_CSV, SUMMARY_COLUMNS, row)


def safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------- Routes ----------
@app.route("/")
def index():
    _, _, conditions = load_data()
    return render_template("index.html", conditions=conditions.to_dict(orient="records"))


@app.route("/experiment")
def experiment():
    subject_id = (request.args.get("subject") or "").strip()
    condition_id = (request.args.get("condition") or "").strip()
    # Agent metadata — optional URL params. Defaults to human participant.
    participant_type = (request.args.get("participant_type") or "human").strip()
    agent_framework = (request.args.get("agent_framework") or "").strip()
    agent_model = (request.args.get("agent_model") or "").strip()
    if not subject_id or not condition_id:
        return redirect(url_for("index"))
    # Human learning-effect control (opt-in via ?shuffle=1). Default OFF so agent
    # runs and existing behavior are byte-for-byte unchanged.
    shuffle = (request.args.get("shuffle") or "").strip().lower() in ("1", "true", "on", "yes")
    payload = get_experiment_payload(condition_id)
    if payload is None:
        return f"Unknown condition: {condition_id}", 400
    started_at_ms = int(time.time() * 1000)

    # Build display rows. The UNDERLYING apartment set is unchanged (head n_alt),
    # so stimulus content stays identical to the agent runs (parity). When shuffle
    # is on we permute which underlying apartment sits in each display row and
    # RELABEL rows A,B,C... by screen position, so the visible label carries no
    # cross-trial identity. Values are still looked up / logged / validated by the
    # underlying apartment_id, so /reveal, /submit and the summary schema are
    # untouched. The display_label -> stimulus_alt_id mapping is logged so the
    # true choice is always recoverable.
    apts = list(payload["apartments"])
    order = list(range(len(apts)))
    shuffle_seed = ""
    if shuffle:
        shuffle_seed = int.from_bytes(os.urandom(4), "big")
        random.Random(shuffle_seed).shuffle(order)
    rows = [{"display_label": chr(ord("A") + pos),
             "stimulus_alt_id": str(apts[idx]["apartment_id"])}
            for pos, idx in enumerate(order)]
    display_map = {r["display_label"]: r["stimulus_alt_id"] for r in rows}

    # Log the mapping server-side (single authoritative write). Guarded so a
    # logging failure never blocks the page. Only when shuffled, to keep
    # non-shuffle (agent) event logs exactly as before.
    if shuffle:
        try:
            map_str = "seed=%s;%s" % (
                shuffle_seed,
                ";".join("%s=%s" % (r["display_label"], r["stimulus_alt_id"]) for r in rows),
            )
            append_event(event_log_path(subject_id, condition_id, started_at_ms), {
                "timestamp": datetime.utcnow().isoformat(),
                "subject_id": subject_id,
                "condition_id": condition_id,
                "event_type": "stimulus_map",
                "apartment_id": "",
                "dimension_id": "",
                "value": map_str,
                "elapsed_ms": 0,
            })
        except Exception as e:
            print("[/experiment stimulus_map log failed] %s: %s"
                  % (type(e).__name__, e), file=sys.stderr, flush=True)

    return render_template(
        "experiment.html",
        subject_id=subject_id,
        condition_id=condition_id,
        condition=payload["condition"],
        rows=rows,
        dimensions=payload["dimensions"],
        started_at_ms=started_at_ms,
        participant_type=participant_type,
        agent_framework=agent_framework,
        agent_model=agent_model,
        shuffle=1 if shuffle else 0,
        display_map=display_map,
    )


@app.route("/reveal", methods=["POST"])
def reveal():
    try:
        data = request.get_json(force=True, silent=True) or {}
        required = (
            "subject_id",
            "condition_id",
            "started_at_ms",
            "apartment_id",
            "dimension_id",
        )
        if not all(k in data for k in required):
            return jsonify({"ok": False, "error": "missing fields"}), 400

        subject_id = (data.get("subject_id") or "").strip()
        condition_id = (data.get("condition_id") or "").strip()
        apartment_id = (data.get("apartment_id") or "").strip()
        dimension_id = (data.get("dimension_id") or "").strip()
        started_at_ms = safe_int(data.get("started_at_ms"))
        elapsed_ms = safe_int(data.get("elapsed_ms"))

        value, error = get_reveal_value(condition_id, apartment_id, dimension_id)
        if error:
            return jsonify({"ok": False, "error": error}), 400

        path = event_log_path(subject_id, condition_id, started_at_ms)
        append_event(path, {
            "timestamp": datetime.utcnow().isoformat(),
            "subject_id": subject_id,
            "condition_id": condition_id,
            "event_type": "info_view",
            "apartment_id": apartment_id,
            "dimension_id": dimension_id,
            "value": value,
            "elapsed_ms": elapsed_ms,
        })
        return jsonify({"ok": True, "value": value})
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[/reveal ERROR] {type(e).__name__}: {e}\n{tb}", file=sys.stderr, flush=True)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/log", methods=["POST"])
def log_event():
    try:
        data = request.get_json(force=True, silent=True) or {}
        required = ("subject_id", "condition_id", "event_type", "started_at_ms")
        if not all(k in data for k in required):
            return jsonify({"ok": False, "error": "missing fields"}), 400

        condition_id = (data.get("condition_id") or "").strip()
        event_type = (data.get("event_type") or "").strip()
        if event_type not in {"session_start", "choice_select", "raw_click"}:
            return jsonify({"ok": False, "error": f"unsupported event_type: {event_type!r}"}), 400

        if event_type == "choice_select":
            payload = get_experiment_payload(condition_id)
            if payload is None:
                return jsonify({"ok": False, "error": f"unknown condition: {condition_id!r}"}), 400
            valid_choices = {str(apt.get("apartment_id")) for apt in payload["apartments"]}
            if str(data.get("apartment_id", "")) not in valid_choices:
                return jsonify({"ok": False, "error": "choice not in condition"}), 400

        path = event_log_path(data["subject_id"], data["condition_id"], data["started_at_ms"])
        row = {
            "timestamp": datetime.utcnow().isoformat(),
            "subject_id": data["subject_id"],
            "condition_id": condition_id,
            "event_type": event_type,
            "apartment_id": data.get("apartment_id", ""),
            "dimension_id": data.get("dimension_id", ""),
            "value": data.get("value", ""),
            "elapsed_ms": data.get("elapsed_ms", ""),
            "x": data.get("x", ""),
            "y": data.get("y", ""),
        }
        append_event(path, row)
        return jsonify({"ok": True})
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[/log ERROR] {type(e).__name__}: {e}\n{tb}", file=sys.stderr, flush=True)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/submit", methods=["POST"])
def submit():
    try:
        data = request.get_json(force=True, silent=True) or request.form.to_dict()
        subject_id = (data.get("subject_id") or "").strip()
        condition_id = (data.get("condition_id") or "").strip()
        final_choice = data.get("final_choice", "") or ""
        total_clicks = safe_int(data.get("total_clicks"))
        unique_info_viewed = safe_int(data.get("unique_info_viewed"))
        total_time_ms = safe_int(data.get("total_time_ms"))
        started_at_ms = safe_int(data.get("started_at_ms"))
        participant_type = (data.get("participant_type") or "human").strip()
        agent_framework = (data.get("agent_framework") or "").strip()
        agent_model = (data.get("agent_model") or "").strip()

        payload = get_experiment_payload(condition_id)
        if payload is None:
            return jsonify({"ok": False, "error": f"unknown condition: {condition_id!r}"}), 400
        valid_choices = {str(apt.get("apartment_id")) for apt in payload["apartments"]}
        if final_choice not in valid_choices:
            return jsonify({"ok": False, "error": "final_choice not in condition"}), 400

        cond = payload["condition"]
        n_alt = int(cond["n_alternatives"])
        n_dim = int(cond["n_dimensions"])
        pct = (unique_info_viewed / (n_alt * n_dim)) if n_alt * n_dim > 0 else 0.0
        summary_row = {
            "subject_id": subject_id,
            "condition_id": condition_id,
            "n_alternatives": n_alt,
            "n_dimensions": n_dim,
            "participant_type": participant_type,
            "agent_framework": agent_framework,
            "agent_model": agent_model,
            "total_time_ms": total_time_ms,
            "total_clicks": total_clicks,
            "unique_info_viewed": unique_info_viewed,
            "pct_info_searched": round(pct, 4),
            "final_choice": final_choice,
            "started_at": datetime.utcfromtimestamp(started_at_ms / 1000).isoformat() if started_at_ms else "",
            "completed_at": datetime.utcnow().isoformat(),
        }
        append_summary(summary_row)
        path = event_log_path(subject_id, condition_id, started_at_ms)
        append_event(path, {
            "timestamp": datetime.utcnow().isoformat(),
            "subject_id": subject_id,
            "condition_id": condition_id,
            "event_type": "final_choice",
            "apartment_id": final_choice,
            "dimension_id": "",
            "value": "",
            "elapsed_ms": total_time_ms,
        })
        append_event(path, {
            "timestamp": datetime.utcnow().isoformat(),
            "subject_id": subject_id,
            "condition_id": condition_id,
            "event_type": "session_end",
            "apartment_id": "",
            "dimension_id": "",
            "value": "",
            "elapsed_ms": total_time_ms,
        })
        return jsonify({"ok": True, "redirect": url_for("complete", subject=subject_id, condition=condition_id)})
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[/submit ERROR] {type(e).__name__}: {e}\n{tb}", file=sys.stderr, flush=True)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/complete")
def complete():
    subject_id = request.args.get("subject", "")
    condition_id = request.args.get("condition", "")
    return render_template("complete.html", subject_id=subject_id, condition_id=condition_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
