import os, sqlite3, secrets
from datetime import datetime, timedelta, timezone, date
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load .env values if available
load_dotenv()

# Render provides its own domain; fallback to localhost for dev
HOST_URL = os.getenv("RENDER_EXTERNAL_URL", "http://127.0.0.1:8000")

DB_PATH = "db.sqlite"

app = FastAPI(title="WiFiBot Licensing")
templates = Jinja2Templates(directory="templates")

# CORS setup (allow API calls from anywhere)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ---------------- Database ----------------
def db():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    con = db(); cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS activation_requests(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id TEXT, hwid TEXT, contact TEXT, upi_txn TEXT,
      status TEXT DEFAULT 'pending', admin_note TEXT, created_at TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS licenses(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id TEXT UNIQUE, hwid TEXT, expiry TEXT, created_at TEXT
    )""")
    con.commit(); con.close()
init_db()

# ---------------- Student JSON API ----------------
@app.post("/api/request-activation")
async def api_request_activation(
    student_id: str = Form(...),
    hwid: str = Form(...),
    contact: str = Form(""),
    upi_txn: str = Form("")
):
    con = db(); cur = con.cursor()
    cur.execute(
        "INSERT INTO activation_requests(student_id,hwid,contact,upi_txn,status,created_at) VALUES(?,?,?,?,?,?)",
        (student_id, hwid, contact, upi_txn, "pending", datetime.now(timezone.utc).isoformat())
    )
    con.commit(); con.close()
    return {"ok": True}

@app.post("/api/check")
async def api_check(payload: dict):
    sid = payload.get("student_id"); hw = payload.get("hwid")
    if not sid or not hw:
        return JSONResponse({"ok": False, "reason": "missing"}, status_code=400)
    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM licenses WHERE student_id=?", (sid,))
    row = cur.fetchone(); con.close()
    if not row:
        return {"ok": False, "state": "blocked", "reason": "no-license"}
    if row["hwid"] != hw:
        return {"ok": False, "state": "blocked", "reason": "hwid-mismatch", "bound_to": row["hwid"]}
    expiry = date.fromisoformat(row["expiry"])
    today = date.today()
    if today <= expiry:
        return {"ok": True, "state": "active", "expiry": row["expiry"]}
    elif today <= (expiry + timedelta(days=7)):
        return {"ok": True, "state": "due", "expiry": row["expiry"]}
    else:
        return {"ok": False, "state": "blocked", "expiry": row["expiry"]}

@app.get("/request", response_class=HTMLResponse)
def request_form(request: Request):
    return templates.TemplateResponse("request.html", {"request": request, "host": HOST_URL})

# ---------------- Admin Panel ----------------
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM activation_requests ORDER BY created_at DESC")
    reqs = cur.fetchall()
    cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
    lic = cur.fetchall()
    con.close()
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "requests": reqs, "licenses": lic, "host": HOST_URL}
    )

@app.post("/admin/approve")
def approve(req_id: int = Form(...), days: int = Form(30)):
    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM activation_requests WHERE id=?", (req_id,))
    r = cur.fetchone()
    if not r:
        con.close(); raise HTTPException(404, "request not found")
    sid, hw = r["student_id"], r["hwid"]
    expiry = (date.today() + timedelta(days=days)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        "INSERT OR REPLACE INTO licenses(student_id, hwid, expiry, created_at) VALUES(?,?,?,?)",
        (sid, hw, expiry, now)
    )
    cur.execute("UPDATE activation_requests SET status=?, admin_note=? WHERE id=?", ("approved", f"Approved {days}d", req_id))
    con.commit(); con.close()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/reject")
def reject(req_id: int = Form(...)):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE activation_requests SET status=?, admin_note=? WHERE id=?", ("rejected", "Rejected", req_id))
    con.commit(); con.close()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/extend")
def extend(student_id: str = Form(...), days: int = Form(30)):
    con = db(); cur = con.cursor()
    cur.execute("SELECT * FROM licenses WHERE student_id=?", (student_id,))
    row = cur.fetchone()
    if not row:
        con.close(); raise HTTPException(404, "license not found")
    base = date.fromisoformat(row["expiry"])
    new_exp = (base + timedelta(days=days)).isoformat()
    cur.execute("UPDATE licenses SET expiry=? WHERE student_id=?", (new_exp, student_id))
    con.commit(); con.close()
    return RedirectResponse("/admin", status_code=303)

@app.post("/admin/revoke")
def revoke(student_id: str = Form(...)):
    con = db(); cur = con.cursor()
    cur.execute("DELETE FROM licenses WHERE student_id=?", (student_id,))
    con.commit(); con.close()
    return RedirectResponse("/admin", status_code=303)
