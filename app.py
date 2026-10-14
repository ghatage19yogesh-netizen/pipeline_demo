"""
Allocation Output Publisher — Streamlit app (planner login only)

Screen 1 · Optimizer output   : load the output of the allocation optimizer (results_output.pkl /
                                CSV / Excel, or demo output), set the run parameters, review it.
Screen 2 · Publish to BigQuery : choose the destination, check, and write the tables to BigQuery.

The optimizer itself is NOT part of this app — it runs in the pipeline (model_main.py).

Demo mode (default): a local SQLite file stands in for BigQuery.
Connected mode: add [gcp_service_account] (and optional [bigquery]) to Streamlit secrets and
install google-cloud-bigquery + pyarrow.

Add real users in secrets — generate a block with:
    python app.py adduser <username> "<Full Name>" <password>
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import pickle
import re
import secrets as pysecrets
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(os.environ.get("ALLOC_DATA_DIR", Path(__file__).resolve().parent / "data"))
DEMO_DB = DATA_DIR / "bigquery_demo.sqlite"
PUB_LOG = DATA_DIR / "publications.json"


# ======================================================================================
# Auth — planner only
# ======================================================================================
def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def make_user(name: str, password: str) -> dict:
    salt = pysecrets.token_hex(16)
    return {"name": name, "role": "planner", "salt": salt, "password_hash": hash_password(password, salt)}


_DEMO_SALT = "8f1d2c3b4a5968778695a4b3c2d1e0f1"
DEMO_USERS = {"yogesh": {"name": "Yogesh", "role": "planner", "salt": _DEMO_SALT,
                         "password_hash": hash_password("planner123", _DEMO_SALT)}}
DEMO_PASSWORD = "planner123"

if __name__ == "__main__" and len(sys.argv) >= 2 and sys.argv[1] == "adduser":
    if len(sys.argv) != 5:
        print('usage: python app.py adduser <username> "<Full Name>" <password>')
        sys.exit(1)
    _u, _n, _p = sys.argv[2:]
    print(f"[users.{_u}]")
    for _k, _v in make_user(_n, _p).items():
        print(f"{_k} = {json.dumps(_v)}")
    sys.exit(0)

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Allocation Planner", page_icon=":material/upload:", layout="wide")
ss = st.session_state
for _k in ["src", "month", "run_id", "env", "model_version", "notes", "gcs_path", "inc_storage", "excl_depts"]:  # keep values across pages
    if _k in ss:
        ss[_k] = ss[_k]


def _secrets() -> dict:
    try:
        return st.secrets
    except Exception:
        return {}


def load_users() -> tuple[dict, bool]:
    try:
        users = {k: dict(v) for k, v in _secrets()["users"].items()}
        users = {k: v for k, v in users.items() if v.get("role", "planner") == "planner"}
        if users:
            return users, False
    except Exception:
        pass
    return DEMO_USERS, True


def check_login(users, username, password):
    u = users.get((username or "").strip().lower())
    if not u:
        hash_password(password or "", "00" * 16)
        return None
    if hmac.compare_digest(hash_password(password or "", u["salt"]), u["password_hash"]):
        return {"username": username.strip().lower(), "name": u["name"]}
    return None


# ======================================================================================
# Demo optimizer output (static sample of results_output.pkl — no optimizer code here)
# ======================================================================================
DEMO_ALLOCATION = [  # warehouse, zone, product_type, storage, units, unit_cost, cost, method
    ('10189', 'East', '410-520-low', 'FLA', 40.0, 2.1, 84.0, 'Slow seller'),
    ('10189', 'Central', '410-520-low', 'FLA', 35.0, 1.2, 42.0, 'Slow seller'),
    ('10189', 'West', '410-520-low', 'FLA', 25.0, 2.1, 52.5, 'Slow seller'),
    ('10189', 'East', '415-525-low', 'HFG', 20.0, 4.9, 98.0, 'Slow seller'),
    ('10189', 'Central', '415-525-low', 'HFG', 10.0, 2.8, 28.0, 'Slow seller'),
    ('10189', 'West', '415-525-low', 'HFG', 30.0, 4.9, 147.0, 'Slow seller'),
    ('9610', '(no demand)', '520-610-high', 'FLA', 20.0, None, 0.0, 'No demand'),
    ('10189', '(no demand)', '520-610-high', 'FLA', 20.0, None, 0.0, 'No demand'),
    ('10255', '(no demand)', '520-610-high', 'FLA', 20.0, None, 0.0, 'No demand'),
    ('9610', 'East', '610-700-high', 'FLA', 80.0, 0.8, 64.0, 'Restricted'),
    ('9610', 'Central', '610-700-high', 'FLA', 60.0, 1.4, 84.0, 'Restricted'),
    ('9610', 'West', '610-700-high', 'FLA', 70.0, 2.0, 140.0, 'Restricted'),
    ('9610', 'East', '101-210-high', 'FLA', 600.0, 2.0, 1200.0, 'Optimizer'),
    ('10189', 'Central', '101-210-high', 'FLA', 300.0, 2.0, 600.0, 'Optimizer'),
    ('10255', 'West', '101-210-high', 'FLA', 400.0, 2.0, 800.0, 'Optimizer'),
    ('9610', 'East', '101-215-high', 'FLA', 450.0, 1.8, 810.0, 'Optimizer'),
    ('10189', 'Central', '101-215-high', 'FLA', 350.0, 1.8, 630.0, 'Optimizer'),
    ('10255', 'West', '101-215-high', 'FLA', 500.0, 1.8, 900.0, 'Optimizer'),
    ('9610', 'East', '202-330-medium', 'HFG', 200.0, 2.4, 480.0, 'Optimizer'),
    ('9610', 'Central', '202-330-medium', 'HFG', 50.0, 4.2, 210.0, 'Optimizer'),
    ('10255', 'Central', '202-330-medium', 'HFG', 100.0, 4.2, 420.0, 'Optimizer'),
    ('10255', 'West', '202-330-medium', 'HFG', 300.0, 2.4, 720.0, 'Optimizer'),
    ('10189', 'East', '202-340-medium', 'HFG', 120.0, 4.55, 546.0, 'Optimizer'),
    ('10189', 'Central', '202-340-medium', 'HFG', 180.0, 2.6, 468.0, 'Optimizer'),
    ('10189', 'West', '202-340-medium', 'HFG', 90.0, 4.55, 409.5, 'Optimizer'),
    ('9610', 'East', '305-410-high', 'HFG', 250.0, 3.0, 750.0, 'Optimizer'),
    ('9610', 'Central', '305-410-high', 'HFG', 200.0, 5.25, 1050.0, 'Optimizer'),
    ('10255', 'West', '305-410-high', 'HFG', 150.0, 3.0, 450.0, 'Optimizer'),
]

DEMO_CAPACITY = {'FLA': {'9610': 2000, '10189': 1500, '10255': 1800}, 'HFG': {'9610': 700, '10189': 450, '10255': 500}}
DEMO_LOCATIONS = {"9610": "East", "10189": "Bailey Road", "10255": "West"}  # demo values — edit in the app
DEMO_LIMITS = {'min_pct': {'9610': 25.0, '10189': 20.0, '10255': 25.0}, 'max_pct': {'9610': 50.0, '10189': 40.0, '10255': 45.0}}

REQUIRED = ["warehouse", "zone", "product_type", "storage", "units"]
OPTIONAL = ["unit_cost", "cost", "method"]

BQ_TABLES = {
    "allocation_plan": {
        "desc": "Units to stock per warehouse × zone × product type, with cost and how it was decided.",
        "schema": [("run_id", "STRING"), ("target_month", "DATE"), ("warehouse_id", "STRING"), ("location", "STRING"), ("zone", "STRING"),
                   ("product_type", "STRING"), ("dept_nbr", "STRING"), ("storage", "STRING"), ("units", "FLOAT64"),
                   ("unit_cost", "FLOAT64"), ("cost", "FLOAT64"), ("method", "STRING"),
                   ("published_at", "TIMESTAMP")]},
    "capacity_summary": {
        "desc": "Space used per warehouse × storage type, with overflow above capacity.",
        "schema": [("run_id", "STRING"), ("target_month", "DATE"), ("warehouse_id", "STRING"), ("location", "STRING"), ("storage", "STRING"),
                   ("allocated", "FLOAT64"), ("capacity", "FLOAT64"), ("used_pct", "FLOAT64"),
                   ("overflow", "FLOAT64"), ("published_at", "TIMESTAMP")]},
    "warehouse_share": {
        "desc": "Each warehouse (ID + location) and its share of all units vs its min / max target.",
        "schema": [("run_id", "STRING"), ("target_month", "DATE"), ("warehouse_id", "STRING"), ("location", "STRING"), ("units", "FLOAT64"),
                   ("share_pct", "FLOAT64"), ("min_pct", "FLOAT64"), ("max_pct", "FLOAT64"),
                   ("published_at", "TIMESTAMP")]},
    "run_log": {
        "desc": "One row per publish: run parameters, adjustments made in the app, totals, source and who published it.",
        "schema": [("run_id", "STRING"), ("target_month", "DATE"), ("pipeline_env", "STRING"),
                   ("model_version", "STRING"), ("solver_status", "STRING"), ("source", "STRING"),
                   ("total_units", "FLOAT64"), ("total_cost", "FLOAT64"), ("overflow_units", "FLOAT64"),
                   ("row_count", "INT64"), ("notes", "STRING"), ("adjustments_json", "STRING"), ("published_by", "STRING"),
                   ("published_at", "TIMESTAMP")]},
}
_ID_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_ID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def demo_output() -> dict:
    alloc = pd.DataFrame(DEMO_ALLOCATION, columns=REQUIRED[:4] + ["units", "unit_cost", "cost", "method"])
    cap = pd.DataFrame(DEMO_CAPACITY).rename_axis("warehouse").reset_index().melt(
        id_vars="warehouse", var_name="storage", value_name="capacity")
    lim = pd.DataFrame(DEMO_LIMITS).rename_axis("warehouse").reset_index()
    return {"allocation": alloc, "capacity": cap, "limits": lim, "locations": dict(DEMO_LOCATIONS),
            "solver_status": "Optimal",
            "source": "Demo output (sample results_output.pkl)"}


# ======================================================================================
# Reading optimizer output files
# ======================================================================================
def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    alias = {"loc": "warehouse", "cfc": "warehouse", "dest": "zone", "destination": "zone", "prod_type": "product_type",
             "strg_type": "storage", "storage_type": "storage", "supply": "units", "allocation": "units"}
    return df.rename(columns={k: v for k, v in alias.items() if k in df.columns and v not in df.columns})


def read_table_file(f) -> pd.DataFrame:
    name = f.name.lower()
    if name.endswith(".csv"):
        return _norm(pd.read_csv(f))
    if name.endswith((".xlsx", ".xls")):
        return _norm(pd.read_excel(f))
    raise ValueError("Use a .csv or .xlsx file")


def read_results_pickle(data: bytes) -> dict:
    """Accepts a DataFrame, or a dict / tuple holding DataFrames (allocation + optional capacity / limits)."""
    obj = pickle.loads(data)  # only for files from your own pipeline
    out = {}
    if isinstance(obj, pd.DataFrame):
        out["allocation"] = _norm(obj)
    elif isinstance(obj, dict):
        for key in ("allocation", "detail", "allocation_plan", "results", "final_results"):
            if isinstance(obj.get(key), pd.DataFrame):
                out["allocation"] = _norm(obj[key]); break
        for key in ("capacity", "storage_capacity", "new_storage_cap"):
            if isinstance(obj.get(key), pd.DataFrame):
                out["capacity"] = _norm(obj[key]); break
        if isinstance(obj.get("limits"), pd.DataFrame):
            out["limits"] = _norm(obj["limits"])
        k = obj.get("kpis") or {}
        out["solver_status"] = k.get("status") or obj.get("status")
    elif isinstance(obj, (list, tuple)):
        dfs = [x for x in obj if isinstance(x, pd.DataFrame)]
        if dfs:
            out["allocation"] = _norm(dfs[0])
    if "allocation" not in out:
        raise ValueError("No allocation table found in the pickle (expected a DataFrame or a dict with "
                         "'allocation' / 'detail').")
    return out


def validate_output(out: dict) -> list[dict]:
    a = out.get("allocation")
    res = []
    if a is None or a.empty:
        return [{"level": "error", "text": "No allocation rows"}]
    miss = [c for c in REQUIRED if c not in a.columns]
    res.append({"level": "error" if miss else "ok",
                "text": "Required columns present" if not miss else f"Missing columns: {', '.join(miss)}"})
    if miss:
        return res
    units = pd.to_numeric(a["units"], errors="coerce")
    bad = int(units.isna().sum())
    res.append({"level": "error" if bad else "ok", "text": "Units are numbers" if not bad else f"{bad} non-numeric units"})
    neg = int((units < 0).sum())
    res.append({"level": "error" if neg else "ok", "text": "No negative units" if not neg else f"{neg} negative units"})
    nk = int(a[["warehouse", "product_type", "storage"]].isna().sum().sum())
    res.append({"level": "error" if nk else "ok",
                "text": "No missing warehouse / product / storage" if not nk else f"{nk} missing key values"})
    dup = int(a.duplicated(subset=["warehouse", "zone", "product_type"]).sum())
    res.append({"level": "warn" if dup else "ok",
                "text": "No duplicate warehouse × zone × product rows" if not dup else f"{dup} duplicate rows"})
    res.append({"level": "ok" if out.get("capacity") is not None else "warn",
                "text": "Capacity included" if out.get("capacity") is not None
                else "No capacity file — capacity_summary will show allocation only"})
    st_ = out.get("solver_status")
    if st_:
        res.append({"level": "ok" if st_ == "Optimal" else "warn", "text": f"Solver status: {st_}"})
    return res


def derive_tables(out: dict, meta: dict, published_by: str, published_at: str) -> dict:
    a = out["allocation"].copy()
    a["units"] = pd.to_numeric(a["units"], errors="coerce").fillna(0)
    for c in OPTIONAL:
        if c not in a.columns:
            a[c] = None
    if a["cost"].isna().all() and a["unit_cost"].notna().any():
        a["cost"] = pd.to_numeric(a["unit_cost"], errors="coerce") * a["units"]
    common = {"run_id": meta["run_id"], "target_month": meta["target_month"], "published_at": published_at}

    locs = out.get("locations") or {}

    def frame(df, name):
        df = df.copy()
        if "warehouse" in df.columns:
            df["warehouse_id"] = df["warehouse"].astype(str)
            df["location"] = df["warehouse_id"].map(locs)
        if "product_type" in df.columns:
            df["dept_nbr"] = df["product_type"].astype(str).str.split("-").str[0]
        for k, v in common.items():
            df[k] = v
        cols = [c for c, _ in BQ_TABLES[name]["schema"]]
        for c in cols:
            if c not in df.columns:
                df[c] = None
        return df[cols].reset_index(drop=True)

    alloc_ws = a.groupby(["warehouse", "storage"], as_index=False)["units"].sum().rename(columns={"units": "allocated"})
    if out.get("capacity") is not None:
        cap = out["capacity"][["warehouse", "storage", "capacity"]]
        cs = alloc_ws.merge(cap, on=["warehouse", "storage"], how="outer").fillna({"allocated": 0})
        capn = pd.to_numeric(cs["capacity"], errors="coerce")
        cs["used_pct"] = (100 * cs["allocated"] / capn.where(capn > 0)).round(1)
        cs["overflow"] = (cs["allocated"] - cs["capacity"]).clip(lower=0).round(1)
    else:
        cs = alloc_ws.assign(capacity=None, used_pct=None, overflow=None)
    tot = a["units"].sum()
    sh = a.groupby("warehouse", as_index=False)["units"].sum()
    sh["share_pct"] = (100 * sh["units"] / tot).round(1) if tot else 0
    if out.get("limits") is not None:
        lim = out["limits"].assign(warehouse=out["limits"]["warehouse"].astype(str))
        sh = sh.merge(lim[["warehouse", "min_pct", "max_pct"]], on="warehouse", how="left")
    overflow = float(pd.to_numeric(cs["overflow"], errors="coerce").fillna(0).sum())
    log = pd.DataFrame([{**common, "pipeline_env": meta["env"], "model_version": meta["model_version"],
                         "solver_status": out.get("solver_status") or "Unknown", "source": out.get("source"),
                         "total_units": round(float(tot), 2),
                         "total_cost": round(float(pd.to_numeric(a["cost"], errors="coerce").fillna(0).sum()), 2),
                         "overflow_units": overflow, "row_count": len(a), "notes": meta["notes"],
                         "adjustments_json": json.dumps(meta.get("adjustments", {})),
                         "published_by": published_by}])
    return {"allocation_plan": frame(a, "allocation_plan"), "capacity_summary": frame(cs, "capacity_summary"),
            "warehouse_share": frame(sh, "warehouse_share"), "run_log": frame(log, "run_log")}


# ======================================================================================
# BigQuery writers — demo (SQLite) and connected (google-cloud-bigquery)
# ======================================================================================
def bq_live() -> bool:
    try:
        return "gcp_service_account" in _secrets()
    except Exception:
        return False


def demo_publish(tables, dataset, names, month, mode) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = {}
    with sqlite3.connect(DEMO_DB) as con:
        for key, df in tables.items():
            t = f"{dataset}__{names[key]}"
            if mode == "replace" and con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
                con.execute(f'DELETE FROM "{t}" WHERE target_month = ?', (month,))
            df.to_sql(t, con, if_exists="append", index=False)
            rows[key] = len(df)
    return rows


def demo_inventory(dataset) -> pd.DataFrame:
    if not DEMO_DB.exists():
        return pd.DataFrame()
    out = []
    with sqlite3.connect(DEMO_DB) as con:
        for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?",
                                (f"{dataset}__%",)).fetchall():
            c, m, r = con.execute(f'SELECT COUNT(*), COUNT(DISTINCT target_month), COUNT(DISTINCT run_id) '
                                  f'FROM "{n}"').fetchone()
            out.append({"Table": f"{dataset}.{n.split('__', 1)[1]}", "Rows": c, "Months": m, "Runs": r})
    return pd.DataFrame(out)


def demo_query(sql, project, dataset) -> pd.DataFrame:
    q = sql.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", q) or ";" in q:
        raise ValueError("Only a single SELECT query is allowed here.")
    q = q.replace("`", "")
    q = re.sub(rf"\b{re.escape(project)}\.{re.escape(dataset)}\.(\w+)", rf"{dataset}__\1", q)
    q = re.sub(rf"\b{re.escape(dataset)}\.(\w+)", rf"{dataset}__\1", q)
    con = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(q, con)
    finally:
        con.close()


def bq_client(project, location):
    from google.cloud import bigquery
    from google.oauth2 import service_account
    info = dict(_secrets()["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(info)
    return bigquery.Client(project=project or info.get("project_id"), credentials=creds, location=location)


def bq_publish(client, tables, project, dataset, names, month, mode) -> dict:
    from google.cloud import bigquery
    client.create_dataset(bigquery.Dataset(f"{project}.{dataset}"), exists_ok=True)
    rows = {}
    for key, df in tables.items():
        table_id = f"{project}.{dataset}.{names[key]}"
        schema = [bigquery.SchemaField(n, t) for n, t in BQ_TABLES[key]["schema"]]
        tbl = bigquery.Table(table_id, schema=schema)
        tbl.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.MONTH,
                                                          field="target_month")
        tbl.description = BQ_TABLES[key]["desc"]
        client.create_table(tbl, exists_ok=True)
        if mode == "replace":
            client.query(f"DELETE FROM `{table_id}` WHERE target_month = @m",
                         job_config=bigquery.QueryJobConfig(query_parameters=[
                             bigquery.ScalarQueryParameter("m", "DATE", month)])).result()
        df = df.copy()
        df["target_month"] = pd.to_datetime(df["target_month"]).dt.date
        df["published_at"] = pd.to_datetime(df["published_at"])
        job = client.load_table_from_dataframe(df, table_id, job_config=bigquery.LoadJobConfig(
            schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_APPEND))
        job.result()
        rows[key] = job.output_rows
    return rows


def read_pub_log() -> list:
    try:
        return json.loads(PUB_LOG.read_text())
    except Exception:
        return []


def add_pub_log(entry: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = read_pub_log() + [entry]
    PUB_LOG.write_text(json.dumps(log, indent=1, default=str))


# ======================================================================================
# UI
# ======================================================================================
def month_options():
    t = date.today()
    return [date(t.year + (t.month - 1 + i) // 12, (t.month - 1 + i) % 12 + 1, 1).strftime("%B %Y")
            for i in range(-2, 4)]


LANDING_CSS = """
<style>
.lp-wrap{max-width:1080px;margin:0 auto}
.lp-eyebrow{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#5E5E5A;font-weight:600;margin:0 0 14px}
.lp-h1{font-size:clamp(34px,5vw,54px);line-height:1.05;font-weight:700;letter-spacing:-.02em;margin:0 0 18px;color:#1C1C1A}
.lp-h1 em{font-style:normal;color:#C26A1B}
.lp-lead{font-size:18px;line-height:1.55;color:#3D3D3A;max-width:640px;margin:0 0 8px}
.lp-flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;border:1.5px solid #1C1C1A;border-radius:8px;overflow:hidden;margin:8px 0 0;background:#fff}
.lp-node{padding:18px 18px 16px;border-right:1.5px solid #1C1C1A;position:relative}
.lp-node:last-child{border-right:0}
.lp-node.on{background:#1C1C1A;color:#fff}
.lp-node .k{font-size:12px;letter-spacing:.06em;text-transform:uppercase;opacity:.7;margin-bottom:6px}
.lp-node .t{font-weight:600;font-size:16px;margin-bottom:4px}
.lp-node .d{font-size:13.5px;line-height:1.45;opacity:.85}
.lp-sec{font-size:22px;font-weight:700;margin:0 0 4px;color:#1C1C1A}
.lp-sub{color:#5E5E5A;margin:0 0 18px}
.lp-steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}
.lp-step{background:#fff;border:1.5px solid #C9C8C2;border-radius:8px;padding:20px}
.lp-num{display:inline-flex;width:30px;height:30px;border-radius:50%;background:#C26A1B;color:#fff;font-weight:700;align-items:center;justify-content:center;margin-bottom:10px}
.lp-step h3{font-size:17px;margin:0 0 6px;color:#1C1C1A}
.lp-step p{margin:0;color:#3D3D3A;font-size:14.5px;line-height:1.5}
.lp-feat{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0;border-top:1.5px solid #1C1C1A}
.lp-f{padding:18px 18px 18px 0;border-bottom:1px solid #D9D8D2}
.lp-f b{display:block;font-size:15.5px;margin-bottom:4px;color:#1C1C1A}
.lp-f span{color:#3D3D3A;font-size:14px;line-height:1.5}
@media (max-width:640px){.lp-flow{grid-template-columns:1fr}.lp-node{border-right:0;border-bottom:1.5px solid #1C1C1A}.lp-node:last-child{border-bottom:0}}
.lp-tbl{width:100%;border-collapse:collapse;font-size:14px;background:#fff;border:1.5px solid #C9C8C2;border-radius:8px;overflow:hidden}
.lp-tbl th{text-align:left;font-weight:600;padding:10px 14px;border-bottom:1.5px solid #C9C8C2;background:#F4F3EF}
.lp-tbl td{padding:10px 14px;border-bottom:1px solid #E2E1DC;vertical-align:top}
.lp-tbl code{font-size:13px}
.lp-foot{color:#5E5E5A;font-size:13px;border-top:1px solid #D9D8D2;padding-top:14px;margin-top:8px}
</style>
"""


def _go_login():
    ss["view"] = "login"


def landing_page():
    st.markdown(LANDING_CSS, unsafe_allow_html=True)
    top_l, top_r = st.columns([4, 1])
    top_l.markdown("**Allocation Planner**")
    top_r.button("Sign in", key="lp_top_signin", on_click=_go_login, width="stretch")

    st.write("")
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        st.markdown(
            '<div class="lp-wrap"><p class="lp-eyebrow">COM inventory allocation · planner tool</p>'
            '<h1 class="lp-h1">From optimizer results to <em>BigQuery</em>, the results_main.py way.</h1>'
            '<p class="lp-lead">Load the pipeline\'s results and constraints, adjust storage types, building capacity and departments, '
            'check for duplicates, and save clean tables that reports and downstream teams can read.</p></div>',
            unsafe_allow_html=True)
        b1, b2, _ = st.columns([1, 1, 1.2])
        b1.button("Sign in as planner", type="primary", key="lp_signin", on_click=_go_login, width="stretch")
        b2.link_button("How it works ↓", "#how-it-works", width="stretch")
    with right:
        st.write("")
        st.markdown(
            '<div class="lp-flow" role="list" aria-label="Where this app fits">'
            '<div class="lp-node" role="listitem"><div class="k">GCS</div><div class="t">Pipeline files</div>'
            '<div class="d">query_parameters.pkl, results_output.pkl</div></div>'
            '<div class="lp-node on" role="listitem"><div class="k">results_main.py</div><div class="t">Review &amp; adjust</div>'
            '<div class="d">Load results + constraints, adjust, check</div></div>'
            '<div class="lp-node" role="listitem"><div class="k">save_results</div><div class="t">BigQuery</div>'
            '<div class="d">4 tables, partitioned by month</div></div></div>',            unsafe_allow_html=True)
        st.caption("The optimizer itself runs in the pipeline; the app follows each script's steps and shows its log. "
                   "Demo mode simulates BigQuery and GCS.")

    st.write(""); st.write("")
    st.markdown('<div id="how-it-works"></div><h2 class="lp-sec">How it works</h2>'
                '<p class="lp-sub">Sign in, then two screens — the steps of <code>results_main.py</code>.</p>'
                '<div class="lp-steps">'
                '<div class="lp-step"><span class="lp-num">1</span><h3>Review &amp; adjust</h3>'
                '<p>Download <code>query_parameters.pkl</code> (constraints) and <code>results_output.pkl</code>; '
                'adjust storage types, building capacity and departments; run checks.</p></div>'
                '<div class="lp-step"><span class="lp-num">2</span><h3>Save to BigQuery</h3>'
                '<p><code>alloc.save_results</code>: preview, duplicate-plan check, publish the 4 tables, query '
                'what was written.</p></div>'
                '</div>', unsafe_allow_html=True)

    st.write(""); st.write("")
    st.markdown('<h2 class="lp-sec">What you can do</h2><p class="lp-sub">Built around how planners actually '
                'review a monthly plan.</p><div class="lp-feat">'
                '<div class="lp-f"><b>Include, exclude or add storage types</b><span>Drop a storage type from the '
                'publish, or add a new one with its own capacity.</span></div>'
                '<div class="lp-f"><b>Edit building capacity</b><span>Change space per building and storage type; '
                'capacity use updates straight away.</span></div>'
                '<div class="lp-f"><b>Buildings by ID and location</b><span>Every building shows its warehouse ID '
                'and location, in the app and in BigQuery.</span></div>'
                '<div class="lp-f"><b>Exclude departments</b><span>Leave out departments that should not be '
                'published this month.</span></div>'
                '<div class="lp-f"><b>Checks before publishing</b><span>Missing columns, negative units, duplicates '
                'and invalid names block the publish.</span></div>'
                '<div class="lp-f"><b>Safe re-publishing</b><span>Replace mode removes the month\'s old rows first — '
                'no duplicates. Every change is logged.</span></div>'
                '</div>', unsafe_allow_html=True)

    st.write(""); st.write("")
    st.markdown('<h2 class="lp-sec">What lands in BigQuery</h2><p class="lp-sub">Four tables, each partitioned '
                'by <code>target_month</code>.</p>'
                '<table class="lp-tbl"><thead><tr><th scope="col">Table</th><th scope="col">What it holds</th></tr>'
                '</thead><tbody>' + "".join(
                    f'<tr><td><code>{k}</code></td><td>{v["desc"]}</td></tr>' for k, v in BQ_TABLES.items()) +
                '</tbody></table>', unsafe_allow_html=True)

    st.write("")
    c1, c2 = st.columns([3, 1])
    c1.markdown('<p class="lp-foot">Demo mode by default: a local simulator stands in for BigQuery until a '
                'service-account key is added in Secrets.</p>', unsafe_allow_html=True)
    c2.button("Sign in", type="primary", key="lp_bottom_signin", on_click=_go_login, width="stretch")


def login_page():
    users, demo = load_users()
    _, mid, _ = st.columns([1, 1.1, 1])
    with mid:
        st.write(""); st.write("")
        st.button("← Back", key="login_back", on_click=lambda: ss.update(view="landing"))
        st.markdown("## Allocation Planner")
        st.caption("Monthly COM allocation run · planner sign-in")
        with st.form("login", border=True):
            u = st.text_input("Username", placeholder="e.g. yogesh")
            p = st.text_input("Password", type="password")
            ok = st.form_submit_button("Sign in", type="primary", width="stretch")
        if ok:
            user = check_login(users, u, p)
            if user:
                ss["user"] = user
                st.rerun()
            st.error("Username or password is incorrect.")
        if demo:
            with st.expander("Demo account"):
                st.markdown(f"Planner · username `yogesh` · password `{DEMO_PASSWORD}`")


# ======================================================================================
# Pipeline stages — queries_main.py → model_main.py → results_main.py (demo)
#   GCS and BigQuery are simulated locally. The optimizer is NOT in this app: in demo mode
#   model_main's run_optimization step returns a stored solution (the demo results_output.pkl).
# ======================================================================================
import time as _time

PIPELINE_CONFIG = {"PIPELINE_NAME": "scs-com-allocation", "DEPLOY_ENV": "prod", "GCP_PROJECT": "[gcp-project]",
                   "BUCKET_NAME": "[allocation-bucket]", "PROJECT_INTERIM_FOLDER": "interim",
                   "PROJECT_PATH": "gs://[allocation-bucket]/scs-com-allocation"}
GCS_DIR = DATA_DIR / "gcs" / "interim"
GS_PREFIX = f"gs://{PIPELINE_CONFIG['BUCKET_NAME']}/{PIPELINE_CONFIG['PROJECT_INTERIM_FOLDER']}"
DEPT_CLASS = {"101": "Tops", "202": "Dresses & suits", "305": "Outerwear", "410": "Accessories",
              "415": "Accessories", "520": "Hosiery", "610": "Jewellery"}
DEMO_BOOL_PARAMS = [("update_data", True, "Re-run the SQL and rebuild intermediate tables"),
                    ("use_medium_velocity_constraint", True, "Limit medium-velocity products to 2 buildings"),
                    ("allocate_low_velocity_by_rule", True, "Place slow sellers by rule before the LP"),
                    ("relax_capacity_when_short", True, "Soft capacity (with penalty) when demand > space")]
DEMO_INT_PARAMS = [("demand_lookback_months", 6, "Months of sales history used for demand"),
                   ("max_buildings_medium_vel", 2, "Max buildings for a medium-velocity product"),
                   ("medium_vel_max_min_units", 7, "min_units threshold for medium velocity"),
                   ("solver_time_limit_min", 30, "Gurobi time limit")]
_UNUSED_QUERY_PIPELINE = [("01_sales_history", "Sales by product type × zone × month", 162),
                  ("02_demand_forecast", "Demand per product type × zone for the target month", 27),
                  ("03_ship_costs", "Cost per unit for each CFC × zone × product type", 80),
                  ("04_cfc_capacity", "Space per CFC × storage type", 6),
                  ("05_product_attributes", "Storage type, velocity, class per product type", 9),
                  ("06_allocation_restrictions", "Must-be-in / forbidden-in rules", 2),
                  ("07_cfc_alloc_limits", "Min / max share per CFC", 3)]


def _gcs_put(name: str, data: bytes):
    GCS_DIR.mkdir(parents=True, exist_ok=True)
    (GCS_DIR / name).write_bytes(data)


def _gcs_get(name: str) -> bytes:
    p = GCS_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"{GS_PREFIX}/{name} not found")
    return p.read_bytes()


def _gcs_exists(name: str) -> bool:
    return (GCS_DIR / name).exists()


# ---------- demo inputs: what queries_main.py / model_main.py leave in GCS ----------
def make_dataset_demo(month_iso, bool_params, int_params):
    """Demo make_dataset(): the 4 objects saved in query_parameters.pkl."""
    alloc = pd.DataFrame(DEMO_ALLOCATION, columns=REQUIRED[:4] + ["units", "unit_cost", "cost", "method"])
    prods = sorted(alloc["product_type"].unique())
    sets = {"cfcs": sorted(DEMO_LOCATIONS), "destinations": sorted(alloc["zone"].unique()),
            "storages": sorted(DEMO_CAPACITY), "product_types": prods,
            "product_classes": sorted({DEPT_CLASS.get(p.split("-")[0], "Other") for p in prods})}
    dem = alloc.groupby(["zone", "product_type"])["units"].sum()
    params = {"target_month": month_iso,
              "demand": {k: float(v) for k, v in dem.items()},
              "costs": {(w, z, p): float(c) for w, z, p, c in
                        alloc.dropna(subset=["unit_cost"])[["warehouse", "zone", "product_type", "unit_cost"]].values},
              "product_storage": dict(alloc.drop_duplicates("product_type")[["product_type", "storage"]].values),
              "cfc_location": dict(DEMO_LOCATIONS)}
    options = {"bool_params": bool_params, "int_params": int_params, "deploy_env": PIPELINE_CONFIG["DEPLOY_ENV"]}
    cap = pd.DataFrame(DEMO_CAPACITY).rename_axis("warehouse").reset_index().melt(
        id_vars="warehouse", var_name="storage", value_name="capacity")
    lim = pd.DataFrame(DEMO_LIMITS).rename_axis("warehouse").reset_index()
    constraints = {"storage_capacity": cap, "alloc_limits": lim,
                   "exceptions": pd.DataFrame([("610-700-high", "must_be_in", "9610"),
                                               ("305-410-high", "forbidden_in", "10189")],
                                              columns=["product_type", "rule", "cfcs"])}
    return (sets, params, options, constraints)


def ensure_demo_gcs(month_label: str):
    """Demo: place the files the pipeline would leave in GCS (query_parameters.pkl, results_output.pkl)."""
    month_iso = datetime.strptime(month_label, "%B %Y").date().isoformat()
    if not _gcs_exists("query_parameters.pkl"):
        clf = make_dataset_demo(month_iso, {k: v for k, v, _ in DEMO_BOOL_PARAMS},
                                {k: v for k, v, _ in DEMO_INT_PARAMS})
        _gcs_put("query_parameters.pkl", pickle.dumps(clf))
    if not _gcs_exists("results_output.pkl"):
        alloc = pd.DataFrame(DEMO_ALLOCATION, columns=REQUIRED[:4] + ["units", "unit_cost", "cost", "method"])
        _gcs_put("results_output.pkl", pickle.dumps({
            "detail": alloc, "target_month": month_iso,
            "kpis": {"status": "Optimal", "units": float(alloc["units"].sum()), "total_cost": float(alloc["cost"].sum())}}))


def load_pipeline_output() -> dict:
    """results_main.py: download query_parameters.pkl (constraints = clf[3]) and results_output.pkl."""
    clf = pickle.loads(_gcs_get("query_parameters.pkl"))
    constraints = clf[3]
    results = pickle.loads(_gcs_get("results_output.pkl"))
    out = {"allocation": results["detail"].copy(), "capacity": constraints["storage_capacity"].copy(),
           "limits": constraints["alloc_limits"].copy(), "locations": dict(clf[1].get("cfc_location", {})),
           "solver_status": results["kpis"]["status"], "source": f"{GS_PREFIX}/results_output.pkl",
           "target_month": results.get("target_month")}
    return out


P_REVIEW, P_PUBLISH = ("results_main.py · Review & adjust", "results_main.py · Save to BigQuery")

SRC_MAP = {  # which src/ modules each page follows
    P_REVIEW: ("src/results_main.py", "process_results()",
               ["data/gbq_connection", "utils/gcs_storage", "utils/alloc_utils.load_pickle", "config.py"]),
    P_PUBLISH: ("src/results_main.py", "alloc.save_results(results, constraints, gbq_client)",
                ["utils/alloc_utils.save_results", "data/gbq_connection", "config.py"]),
}


def code_header(page: str, title: str):
    path, func, mods = SRC_MAP[page]
    st.markdown(f"<p style='margin:0 0 2px;color:#5E5E5A;font-size:14px'>scs-com-allocation / "
                + " / ".join(path.split("/")) + f" → <code>{func}</code></p>", unsafe_allow_html=True)
    st.markdown(f"## {title}")
    st.markdown("Uses " + " ".join(f":gray-badge[{m}]" for m in mods))
ICON = {"ok": ":green[✓]", "warn": ":orange[!]", "error": ":red[✕]"}


def page_output():
    code_header(P_REVIEW, "Review & adjust")
    st.caption("`process_results()` downloads `query_parameters.pkl` (constraints = `clf[3]`) and "
               "`results_output.pkl`. Review them, set the run parameters and adjust before saving to BigQuery.")

    st.markdown("#### Source")
    src_opts = ["GCS · interim folder (demo)", "Upload file", "GCS path"]
    if ss.get("src") not in src_opts:
        ss["src"] = src_opts[0]
    src = st.radio("Where is the output?", src_opts, horizontal=True, key="src", label_visibility="collapsed")
    out = None
    if src.startswith("GCS · interim"):
        ensure_demo_gcs(ss.get("month", month_options()[2]))
        out = load_pipeline_output()
        st.caption(f"Downloaded `{GS_PREFIX}/query_parameters.pkl` → `constraints = clf[3]` (capacity, limits) and "
                   f"`{GS_PREFIX}/results_output.pkl` → `results`. Demo: the bucket is simulated.")
    elif src == "Upload file":
        c1, c2 = st.columns(2)
        f = c1.file_uploader("Allocation output (results_output.pkl, .csv or .xlsx)", type=["pkl", "csv", "xlsx"])
        capf = c2.file_uploader("Capacity (optional .csv / .xlsx: warehouse, storage, capacity)", type=["csv", "xlsx"])
        st.caption("Only upload .pkl files produced by your own pipeline — pickle files can run code when opened. "
                   f"Columns needed: {', '.join(REQUIRED)} (optional: {', '.join(OPTIONAL)}).")
        if f:
            try:
                if f.name.lower().endswith(".pkl"):
                    out = read_results_pickle(f.getvalue())
                else:
                    out = {"allocation": read_table_file(f)}
                out["source"] = f"Upload: {f.name}"
                if capf:
                    out["capacity"] = read_table_file(capf)
            except Exception as e:
                st.error(f"Could not read the file: {type(e).__name__}: {e}")
    else:
        ss.setdefault("gcs_path", "gs://[allocation-bucket]/interim/results_output.pkl")
        st.text_input("GCS path", key="gcs_path")
        if not bq_live():
            st.info("Reading from GCS needs the service account in secrets (connected mode). "
                    "Use **Demo output** or **Upload file** for now.")
        else:
            if st.button("Load from GCS"):
                try:
                    from google.cloud import storage
                    from google.oauth2 import service_account
                    info = dict(_secrets()["gcp_service_account"])
                    cl = storage.Client(credentials=service_account.Credentials.from_service_account_info(info),
                                        project=info.get("project_id"))
                    m = re.match(r"gs://([^/]+)/(.+)", ss["gcs_path"])
                    data = cl.bucket(m.group(1)).blob(m.group(2)).download_as_bytes()
                    ss["gcs_out"] = {**read_results_pickle(data), "source": ss["gcs_path"]}
                except Exception as e:
                    st.error(f"{type(e).__name__}: {e}")
            out = ss.get("gcs_out")

    if out is None:
        return

    st.markdown("#### Run parameters")
    st.caption("These travel with the data to BigQuery (`run_id`, `target_month` on every row; the rest in `run_log`).")
    c1, c2, c3, c4 = st.columns(4)
    ss.setdefault("run_id", "RUN-" + datetime.now().strftime("%y%m%d-%H%M"))
    ss.setdefault("month", month_options()[2])
    ss.setdefault("model_version", "alloc_solver_v2")
    month = c1.selectbox("Target month", month_options(), key="month")
    run_id = c2.text_input("Run ID", key="run_id")
    env = c3.selectbox("Pipeline environment", ["prod", "dev"], key="env")
    mv = c4.text_input("Model version", key="model_version")
    notes = st.text_input("Notes (optional)", key="notes", placeholder="e.g. Re-run after capacity update for CFC 3")

    checks = validate_output(out)
    if any(c["level"] == "error" for c in checks):
        st.markdown("#### Checks")
        for c in checks:
            st.markdown(f"{ICON[c['level']]} &nbsp; {c['text']}")
        return

    adj_out, adjustments = adjust_section(out)
    a = adj_out["allocation"]
    checks = validate_output(adj_out) if len(a) else [{"level": "error", "text": "Nothing left after the exclusions"}]
    errors = any(c["level"] == "error" for c in checks)

    st.markdown("#### Output after adjustments")
    if len(a):
        units = pd.to_numeric(a["units"], errors="coerce")
        cost = pd.to_numeric(a["cost"], errors="coerce") if "cost" in a else None
        base_units = pd.to_numeric(out["allocation"]["units"], errors="coerce").sum()
        k = st.columns(4)
        k[0].metric("Rows", f"{len(a):,}", delta=f"{len(a) - len(out['allocation']):,}" if len(a) != len(out["allocation"]) else None, border=True)
        k[1].metric("Units", f"{units.sum():,.0f}", delta=f"{units.sum() - base_units:,.0f}" if abs(units.sum() - base_units) > 0.5 else None, border=True)
        k[2].metric("Shipping cost", f"${cost.sum():,.0f}" if cost is not None else "—", border=True)
        k[3].metric("Buildings / products", f"{a['warehouse'].nunique()} / {a['product_type'].nunique()}", border=True)
        tables = derive_tables(adj_out, {"run_id": "preview", "target_month": "", "env": "", "model_version": "",
                                         "notes": ""}, "", "")
        t1, t2, t3 = st.tabs(["Warehouse share", "Capacity use", "Allocation rows"])
        with t1:
            st.dataframe(tables["warehouse_share"][["warehouse_id", "location", "units", "share_pct", "min_pct", "max_pct"]],
                         hide_index=True, width="stretch", column_config={
                             "warehouse_id": "Warehouse ID", "location": "Location",
                             "units": st.column_config.NumberColumn("Units", format="%d"),
                             "share_pct": st.column_config.NumberColumn("Share", format="%.1f%%"),
                             "min_pct": st.column_config.NumberColumn("Min %", format="%.0f%%"),
                             "max_pct": st.column_config.NumberColumn("Max %", format="%.0f%%")})
        with t2:
            cs = tables["capacity_summary"][["warehouse_id", "location", "storage", "allocated", "capacity", "used_pct", "overflow"]]
            top = max(100.0, float(pd.to_numeric(cs["used_pct"], errors="coerce").fillna(0).max()))
            st.dataframe(cs, hide_index=True, width="stretch", column_config={
                "warehouse_id": "Warehouse ID", "location": "Location", "storage": "Storage",
                "allocated": st.column_config.NumberColumn("Allocated", format="%d"),
                "capacity": st.column_config.NumberColumn("Capacity", format="%d"),
                "used_pct": st.column_config.ProgressColumn("Used", min_value=0, max_value=top, format="%.0f%%"),
                "overflow": st.column_config.NumberColumn("Over by", format="%d")})
        with t3:
            st.dataframe(tables["allocation_plan"].drop(columns=["run_id", "target_month", "published_at"]),
                         hide_index=True, width="stretch")

    st.markdown("#### Checks")
    for c in checks:
        st.markdown(f"{ICON[c['level']]} &nbsp; {c['text']}")
    if st.button("Use this output → Save to BigQuery", type="primary", disabled=errors):
        ss["output"] = adj_out
        ss["meta"] = {"run_id": run_id.strip(), "month_label": month,
                      "target_month": datetime.strptime(month, "%B %Y").date().isoformat(),
                      "env": env, "model_version": mv, "notes": notes, "adjustments": adjustments}
        ss["_goto"] = P_PUBLISH
        st.rerun()


# ---------- adjustments: storage types, buildings (location + capacity), departments ----------
def _dept(pt) -> str:
    return str(pt).split("-")[0]


def _init_adjust(out: dict, sig):
    a = out["allocation"]
    cap = out.get("capacity")
    locs = dict(out.get("locations") or {})
    if "location" in a.columns:
        locs.update({str(w): l for w, l in a[["warehouse", "location"]].dropna().drop_duplicates().values})
    storages = sorted(set(a["storage"].astype(str)) | (set(cap["storage"].astype(str)) if cap is not None else set()))
    whs = sorted(set(a["warehouse"].astype(str)) | (set(cap["warehouse"].astype(str)) if cap is not None else set()))
    wide = pd.DataFrame({"warehouse_id": whs, "location": [locs.get(w, "") for w in whs]})
    for k in storages:
        if cap is not None:
            m = cap.assign(warehouse=cap["warehouse"].astype(str), storage=cap["storage"].astype(str))
            m = m[m["storage"] == k].set_index("warehouse")["capacity"]
            wide[k] = wide["warehouse_id"].map(m).astype(float)
        else:
            wide[k] = float("nan")
    ss.update({"adj_sig": sig, "storage_list": storages, "added_storage": [], "bld_saved": wide.copy(),
               "bld_orig": wide.copy(), "bld_ver": ss.get("bld_ver", 0) + 1, "inc_storage": storages,
               "excl_depts": []})


def _add_storage():
    name = (ss.get("new_storage") or "").strip().upper()
    if not name or not re.match(r"^[A-Z0-9_ -]{1,20}$", name) or name in ss["storage_list"]:
        ss["add_msg"] = "Enter a new, short name (letters, digits, _ or -)."
        return
    ss["storage_list"] = ss["storage_list"] + [name]
    ss["added_storage"] = ss["added_storage"] + [name]
    ss["inc_storage"] = list(ss.get("inc_storage", [])) + [name]
    b = ss["bld_saved"].copy(); b[name] = 0.0
    ss["bld_saved"] = b; ss["bld_ver"] += 1
    ss["new_storage"] = ""; ss["add_msg"] = f"Added storage type {name} — set its capacity under Buildings."


def adjust_section(out: dict):
    sig = (out.get("source"), len(out["allocation"]), tuple(out["allocation"].columns))
    if ss.get("adj_sig") != sig:
        _init_adjust(out, sig)
    a = out["allocation"].copy()
    a["warehouse"] = a["warehouse"].astype(str); a["storage"] = a["storage"].astype(str)
    a["units"] = pd.to_numeric(a["units"], errors="coerce")

    st.markdown("#### Adjust before publishing")
    t1, t2, t3 = st.tabs(["Storage types", "Buildings: location & capacity", "Departments"])
    with t1:
        st.caption("Include or exclude storage types, or add a new one. Rows of excluded types are not published.")
        inc = st.multiselect("Storage types to include", ss["storage_list"], key="inc_storage")
        c1, c2 = st.columns([2, 1])
        c1.text_input("New storage type", key="new_storage", placeholder="e.g. BULK")
        c2.write(""); c2.write("")
        c2.button("Add storage type", on_click=_add_storage, width="stretch")
        if ss.get("add_msg"):
            st.caption(ss.pop("add_msg"))
        st.dataframe(a.groupby("storage", as_index=False).agg(rows=("units", "size"), units=("units", "sum"))
                     .assign(included=lambda d: d["storage"].isin(inc)), hide_index=True, width="stretch")
    with t2:
        st.caption("Each building is identified by its ID and location. Edit the location or the capacity "
                   "(units) per storage type — the capacity summary uses these numbers.")
        cols = ["warehouse_id", "location"] + [k for k in ss["storage_list"] if k in ss["bld_saved"].columns]
        edited = st.data_editor(ss["bld_saved"][cols], key=f"bld_{ss['bld_ver']}", hide_index=True, width="stretch",
                                disabled=["warehouse_id"],
                                column_config={"warehouse_id": "Warehouse ID", "location": "Location",
                                               **{k: st.column_config.NumberColumn(f"{k} capacity", min_value=0,
                                                                                   step=50, format="%d")
                                                  for k in ss["storage_list"]}})
        if not edited.equals(ss["bld_saved"][cols]):
            ss["bld_saved"] = edited.copy(); ss["bld_ver"] += 1
            st.rerun()
        if st.button("Reset buildings to the optimizer's values"):
            keep = ss["bld_orig"].copy()
            for k in ss["added_storage"]:
                keep[k] = 0.0
            ss["bld_saved"] = keep; ss["bld_ver"] += 1
            st.rerun()
    with t3:
        a["dept"] = a["product_type"].map(_dept)
        depts = sorted(a["dept"].unique())
        excl = st.multiselect("Departments to exclude", depts, key="excl_depts")
        st.dataframe(a.groupby("dept", as_index=False).agg(product_types=("product_type", "nunique"),
                                                           units=("units", "sum"))
                     .assign(excluded=lambda d: d["dept"].isin(excl)).rename(columns={"dept": "department"}),
                     hide_index=True, width="stretch")

    # apply
    keep = a["storage"].isin(inc) & ~a["product_type"].map(_dept).isin(excl)
    a2 = out["allocation"][keep.values].reset_index(drop=True)
    a2["warehouse"] = a2["warehouse"].astype(str)
    a2["storage"] = a2["storage"].astype(str)
    b = ss["bld_saved"]
    cap_long = b.melt(id_vars=["warehouse_id", "location"], var_name="storage", value_name="capacity") \
        .rename(columns={"warehouse_id": "warehouse"})
    cap_long = cap_long[cap_long["storage"].isin(inc)].dropna(subset=["capacity"])[["warehouse", "storage", "capacity"]]
    locs = {str(w): (l or "") for w, l in b[["warehouse_id", "location"]].values}
    orig = ss["bld_orig"]
    cap_changes = []
    for _, r in b.iterrows():
        o = orig[orig["warehouse_id"] == r["warehouse_id"]]
        for k in ss["storage_list"]:
            old = float(o[k].iloc[0]) if len(o) and k in o.columns and pd.notna(o[k].iloc[0]) else None
            new = float(r[k]) if pd.notna(r[k]) else None
            if old != new and not (old is None and not new):
                cap_changes.append({"warehouse_id": r["warehouse_id"], "storage": k, "from": old, "to": new})
    loc_changes = {w: l for w, l in locs.items()
                   if l != (orig.set_index("warehouse_id")["location"].get(w, "") or "")}
    adjustments = {"excluded_storage": [k for k in ss["storage_list"] if k not in inc],
                   "added_storage": ss["added_storage"], "excluded_departments": excl,
                   "capacity_changes": cap_changes, "location_changes": loc_changes}
    n = sum(bool(v) for v in adjustments.values())
    if n:
        st.info("Adjustments: " + "; ".join(
            f"{k.replace('_', ' ')}: {len(v)}" for k, v in adjustments.items() if v) +
            " — recorded in `run_log.adjustments_json`.", icon=":material/tune:")
    adj = dict(out, allocation=a2, capacity=cap_long if len(cap_long) else None, locations=locs)
    return adj, adjustments


def page_publish():
    live = bq_live()
    code_header(P_PUBLISH, "Save results to BigQuery")
    if live:
        st.success("Connected mode — writing to real BigQuery.", icon=":material/cloud_done:")
    else:
        st.info("**Demo mode** — a local BigQuery simulator stands in for the dataset. Nothing leaves this app.",
                icon=":material/science:")
    out, meta = ss.get("output"), ss.get("meta")
    if not out:
        st.warning("No optimizer output selected yet.")
        if st.button("Go to 1 · Optimizer output", type="primary"):
            ss["_goto"] = P_REVIEW; st.rerun()
        return
    st.markdown(f"**Run** `{meta['run_id']}` · **{meta['month_label']}** · {meta['env']} · {out.get('source')}")

    cfg = dict(_secrets().get("bigquery", {})) if live else {}
    st.markdown("#### Destination")
    d1, d2, d3, d4 = st.columns([1.3, 1.1, 0.7, 1.4])
    project = d1.text_input("GCP project", value=cfg.get("project", "demo-allocation-project"))
    dataset = d2.text_input("Dataset", value=cfg.get("dataset", "com_allocation"))
    location = d3.text_input("Location", value=cfg.get("location", "US"))
    mode_l = d4.radio("Write mode", ["Replace this month's rows", "Append"],
                      help="Replace removes rows for the same target month first, so publishing again never duplicates.")
    mode = "replace" if mode_l.startswith("Replace") else "append"
    cfg_tbl = st.data_editor(pd.DataFrame([{"publish": True, "output": k, "table": k, "meaning": v["desc"]}
                                           for k, v in BQ_TABLES.items()]), hide_index=True, width="stretch",
                             disabled=["output", "meaning"], key="tblcfg",
                             column_config={"publish": st.column_config.CheckboxColumn("Publish", width="small"),
                                            "output": "Output", "table": "BigQuery table",
                                            "meaning": st.column_config.TextColumn("What it holds", width="large")})
    names = {r["output"]: r["table"] for _, r in cfg_tbl.iterrows() if r["publish"]}

    published_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tables = derive_tables(out, meta, ss["user"]["name"], published_at)
    st.markdown("#### Preview")
    if names:
        tabs = st.tabs([f"{names[k]} · {len(tables[k])} rows" for k in names])
        for tab, k in zip(tabs, names):
            with tab:
                st.caption(f"`{project}.{dataset}.{names[k]}` — partitioned by month on `target_month`")
                st.dataframe(tables[k].head(300), hide_index=True, width="stretch")
                with st.expander("Schema"):
                    st.dataframe(pd.DataFrame(BQ_TABLES[k]["schema"], columns=["column", "type"]),
                                 hide_index=True, width="stretch")

    st.markdown("#### Checks")
    ok_ids = bool(_ID_PROJECT.match(project or "")) and bool(_ID_NAME.match(dataset or "")) and \
        all(_ID_NAME.match(t or "") for t in names.values())
    checks = [
        {"level": "ok" if names else "error", "text": f"{len(names)} table(s) selected"},
        {"level": "ok" if ok_ids else "error",
         "text": "Project, dataset and table names are valid" if ok_ids else
         "Check names: project = lower-case letters/digits/hyphens (6–30); dataset/table = letters, digits, _"},
        {"level": "ok" if meta["run_id"] else "error", "text": "Run ID is set" if meta["run_id"] else "Run ID is empty"},
        {"level": "ok" if abs(tables["allocation_plan"]["units"].sum() - tables["run_log"]["total_units"][0]) < 1
         else "error", "text": f"Totals match ({tables['allocation_plan']['units'].sum():,.0f} units)"},
    ]
    plan_cols = ["warehouse_id", "zone", "product_type", "storage", "units"]
    plan_hash = hashlib.sha256(tables["allocation_plan"][plan_cols].sort_values(plan_cols[:4]).round(2)
                               .to_csv(index=False).encode()).hexdigest()[:16]
    prev = [p for p in read_pub_log() if p["target_month"] == meta["target_month"] and p["target"] == f"{project}.{dataset}"]
    dup = next((p for p in prev if p.get("plan_hash") == plan_hash), None)
    if dup:
        allow_dup = st.checkbox("Publish the duplicate plan anyway", key="allow_dup")
        checks.append({"level": "warn" if allow_dup else "error",
                       "text": f"Duplicate plan: the same plan was already saved for {meta['month_label']} as run "
                               f"{dup['run_id']} on {dup['at']}"})
    else:
        checks.append({"level": "ok", "text": "Not a duplicate of a plan already saved for this month"})
    if prev:
        checks.append({"level": "warn", "text": f"{meta['month_label']} was published before ({len(prev)}×) — "
                       + ("its rows will be replaced" if mode == "replace" else "rows will be added again")})
    for c in checks:
        st.markdown(f"{ICON[c['level']]} &nbsp; {c['text']}")
    blocked = any(c["level"] == "error" for c in checks)

    if st.button(f"Publish {len(names)} table(s) to BigQuery", type="primary", disabled=blocked):
        sub = {k: tables[k] for k in names}
        try:
            with st.spinner("Writing to BigQuery…"):
                rows = (bq_publish(bq_client(project, location), sub, project, dataset, names, meta["target_month"], mode)
                        if live else demo_publish(sub, dataset, names, meta["target_month"], mode))
            add_pub_log({"at": published_at, "by": ss["user"]["name"], "run_id": meta["run_id"],
                         "target_month": meta["target_month"], "target": f"{project}.{dataset}", "mode": mode,
                         "rows": rows, "backend": "bigquery" if live else "demo", "plan_hash": plan_hash})
            ss["last_pub"] = {"rows": rows, "target": f"{project}.{dataset}", "names": names}
            st.rerun()
        except ModuleNotFoundError:
            st.error("Install `google-cloud-bigquery` and `pyarrow` (requirements.txt) for connected mode.")
        except Exception as e:
            st.error(f"Publish failed: {type(e).__name__}: {e}")
    lp = ss.get("last_pub")
    if lp:
        st.success(f"Published to `{lp['target']}`: " +
                   ", ".join(f"**{lp['names'].get(k, k)}** {n:,} rows" for k, n in lp["rows"].items()))

    st.markdown("#### Published data")
    log = read_pub_log()
    if log:
        st.dataframe(pd.DataFrame([{"Published": p["at"], "By": p["by"], "Run": p["run_id"],
                                    "Month": p["target_month"], "Target": p["target"], "Mode": p["mode"],
                                    "Rows": sum(p["rows"].values())} for p in reversed(log)]),
                     hide_index=True, width="stretch")
    if not live:
        inv = demo_inventory(dataset)
        if not inv.empty:
            st.dataframe(inv, hide_index=True, width="stretch")
            plan = names.get("allocation_plan", "allocation_plan")
            sql = st.text_area("Query the published tables (BigQuery-style SQL, SELECT only)", height=140,
                               value=f"SELECT warehouse_id, location, storage, ROUND(SUM(units)) AS units, ROUND(SUM(cost)) AS cost\n"
                                     f"FROM `{project}.{dataset}.{plan}`\nWHERE target_month = '{meta['target_month']}'\n"
                                     f"GROUP BY warehouse_id, location, storage\nORDER BY warehouse_id, storage")
            if st.button("Run query"):
                try:
                    st.dataframe(demo_query(sql, project, dataset), hide_index=True, width="stretch")
                except Exception as e:
                    st.error(f"{type(e).__name__}: {e}")
    else:
        st.caption(f"Open the BigQuery console → `{project}.{dataset}` to see the tables.")

    with st.expander("Connecting to real BigQuery"):
        st.markdown("Add this to the app's **Secrets** and uncomment the two lines in `requirements.txt`:")
        st.code('''[gcp_service_account]   # service-account JSON key, field by field
type = "service_account"
project_id = "your-project"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
client_email = "allocation-app@your-project.iam.gserviceaccount.com"
token_uri = "https://oauth2.googleapis.com/token"

[bigquery]
project = "your-project"
dataset = "com_allocation"
location = "US"''', language="toml")


PAGES = {P_REVIEW: page_output, P_PUBLISH: page_publish}


def main():
    if "user" not in ss:
        if ss.get("view") == "login":
            login_page()
        else:
            landing_page()
        return
    if "_goto" in ss:
        ss["page"] = ss.pop("_goto")
    if ss.get("page") not in PAGES:
        ss["page"] = P_REVIEW
    with st.sidebar:
        st.markdown("### Allocation Planner")
        st.markdown(f"**{ss['user']['name']}**  \nPlanner")
        st.caption("src/")
        st.radio("Navigate", list(PAGES), key="page", label_visibility="collapsed")
        with st.expander("results_main.py · process_results()"):
            st.markdown(
                "1. `get_gbq_client('prod')`, `get_target_month`\n"
                "2. download `query_parameters.pkl` → `constraints = clf[3]`\n"
                "3. download `results_output.pkl` → `results`\n"
                "4. `alloc.save_results(results, constraints, gbq_client)`")
        st.divider()
        if st.button("Log out", width="stretch"):
            for k in list(ss.keys()):
                del ss[k]
            st.rerun()
    PAGES[ss["page"]]()


main()
