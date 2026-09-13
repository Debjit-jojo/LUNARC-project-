from flask import Flask, request, redirect, url_for, render_template_string, jsonify, Response
import sqlite3
from datetime import datetime
import csv
import io

app = Flask(__name__)

DB_NAME = "bloodbank_web.db"
PORT = 5050

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS donors (
            donor_id INTEGER PRIMARY KEY AUTOINCREMENT,
            donor_code TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            age INTEGER NOT NULL,
            gender TEXT,
            blood_group TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT,
            last_donation_date TEXT,
            registration_date TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS blood_stock (
            stock_id INTEGER PRIMARY KEY AUTOINCREMENT,
            blood_group TEXT UNIQUE NOT NULL,
            available_units INTEGER NOT NULL DEFAULT 0,
            last_updated TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS blood_requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_code TEXT UNIQUE NOT NULL,
            patient_name TEXT NOT NULL,
            patient_age INTEGER,
            hospital_name TEXT NOT NULL,
            doctor_name TEXT,
            blood_group TEXT NOT NULL,
            required_units INTEGER NOT NULL,
            fulfilled_units INTEGER NOT NULL DEFAULT 0,
            request_date TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'Normal',
            status TEXT NOT NULL DEFAULT 'Pending',
            notes TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS blood_distribution (
            distribution_id INTEGER PRIMARY KEY AUTOINCREMENT,
            distribution_code TEXT UNIQUE NOT NULL,
            request_id INTEGER NOT NULL,
            patient_name TEXT NOT NULL,
            hospital_name TEXT NOT NULL,
            blood_group TEXT NOT NULL,
            units_distributed INTEGER NOT NULL,
            distribution_date TEXT NOT NULL,
            issued_by TEXT NOT NULL,
            remarks TEXT,
            FOREIGN KEY(request_id) REFERENCES blood_requests(request_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            blood_group TEXT NOT NULL,
            action TEXT NOT NULL,
            change_units INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    for group in BLOOD_GROUPS:
        cur.execute("""
            INSERT OR IGNORE INTO blood_stock
            (blood_group, available_units, last_updated)
            VALUES (?, 0, ?)
        """, (group, now()))

    conn.commit()
    conn.close()


def log_activity(activity_type, message):
    conn = get_db()
    conn.execute("""
        INSERT INTO activity_logs
        (activity_type, message, created_at)
        VALUES (?, ?, ?)
    """, (activity_type, message, now()))
    conn.commit()
    conn.close()


# ============================================================
# DASHBOARD API
# ============================================================

@app.route("/api/dashboard")
def dashboard_api():
    conn = get_db()

    donors = conn.execute(
        "SELECT COUNT(*) AS c FROM donors"
    ).fetchone()["c"]

    units = conn.execute(
        "SELECT COALESCE(SUM(available_units),0) AS c FROM blood_stock"
    ).fetchone()["c"]

    pending = conn.execute(
        "SELECT COUNT(*) AS c FROM blood_requests WHERE status='Pending'"
    ).fetchone()["c"]

    emergency = conn.execute(
        "SELECT COUNT(*) AS c FROM blood_requests "
        "WHERE priority='Emergency' AND status NOT IN ('Completed','Rejected')"
    ).fetchone()["c"]

    distributions = conn.execute(
        "SELECT COUNT(*) AS c FROM blood_distribution"
    ).fetchone()["c"]

    low_stock = conn.execute(
        "SELECT COUNT(*) AS c FROM blood_stock WHERE available_units < 5"
    ).fetchone()["c"]

    stock_rows = conn.execute("""
        SELECT blood_group, available_units
        FROM blood_stock
        ORDER BY stock_id
    """).fetchall()

    status_rows = conn.execute("""
        SELECT status, COUNT(*) AS total
        FROM blood_requests
        GROUP BY status
    """).fetchall()

    priority_rows = conn.execute("""
        SELECT priority, COUNT(*) AS total
        FROM blood_requests
        GROUP BY priority
    """).fetchall()

    activity_rows = conn.execute("""
        SELECT activity_type, message, created_at
        FROM activity_logs
        ORDER BY activity_id DESC
        LIMIT 10
    """).fetchall()

    history_rows = conn.execute("""
        SELECT
            substr(created_at, 1, 10) AS day,
            SUM(CASE WHEN action='ADD' THEN change_units ELSE 0 END) AS added,
            SUM(CASE WHEN action='REMOVE' THEN change_units ELSE 0 END) AS removed
        FROM stock_transactions
        GROUP BY substr(created_at, 1, 10)
        ORDER BY day DESC
        LIMIT 7
    """).fetchall()

    conn.close()

    return jsonify({
        "stats": {
            "donors": donors,
            "units": units,
            "pending": pending,
            "emergency": emergency,
            "distributions": distributions,
            "low_stock": low_stock
        },
        "stock": [
            {
                "group": r["blood_group"],
                "units": r["available_units"]
            }
            for r in stock_rows
        ],
        "statuses": [
            {
                "status": r["status"],
                "total": r["total"]
            }
            for r in status_rows
        ],
        "priorities": [
            {
                "priority": r["priority"],
                "total": r["total"]
            }
            for r in priority_rows
        ],
        "activities": [
            {
                "type": r["activity_type"],
                "message": r["message"],
                "time": r["created_at"]
            }
            for r in activity_rows
        ],
        "history": [
            {
                "day": r["day"],
                "added": r["added"] or 0,
                "removed": r["removed"] or 0
            }
            for r in reversed(history_rows)
        ]
    })


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
def dashboard():
    return render_template_string(HTML)


@app.route("/lab")
def lab():
    return redirect(url_for("dashboard"))


# ============================================================
# DONORS
# ============================================================

@app.route("/donors")
def donors():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM donors
        ORDER BY donor_id DESC
    """).fetchall()
    conn.close()

    return render_template_string(DONORS_HTML, donors=rows)


@app.route("/donors/add", methods=["POST"])
def add_donor():
    name = request.form.get("full_name", "").strip()
    age = request.form.get("age", "").strip()
    gender = request.form.get("gender", "")
    group = request.form.get("blood_group", "")
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    address = request.form.get("address", "").strip()
    last_date = request.form.get("last_donation_date", "")

    if not name or not age or group not in BLOOD_GROUPS:
        return redirect(url_for("donors"))

    try:
        age_value = int(age)
    except ValueError:
        return redirect(url_for("donors"))

    if age_value < 18 or age_value > 65:
        return redirect(url_for("donors"))

    conn = get_db()

    donor_code = "DON-" + datetime.now().strftime("%Y%m%d%H%M%S")

    try:
        conn.execute("""
            INSERT INTO donors
            (donor_code, full_name, age, gender, blood_group,
             phone, email, address, last_donation_date, registration_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            donor_code,
            name,
            age_value,
            gender,
            group,
            phone,
            email,
            address,
            last_date,
            now()
        ))

        conn.commit()

    except sqlite3.IntegrityError:
        conn.close()
        return redirect(url_for("donors"))

    conn.close()

    log_activity(
        "DONOR",
        "New donor registered: " + name + " (" + group + ")"
    )

    return redirect(url_for("donors"))


@app.route("/donors/delete/<int:donor_id>", methods=["POST"])
def delete_donor(donor_id):
    conn = get_db()

    row = conn.execute(
        "SELECT full_name FROM donors WHERE donor_id=?",
        (donor_id,)
    ).fetchone()

    conn.execute(
        "DELETE FROM donors WHERE donor_id=?",
        (donor_id,)
    )

    conn.commit()
    conn.close()

    if row:
        log_activity(
            "DONOR",
            "Donor removed: " + row["full_name"]
        )

    return redirect(url_for("donors"))


# ============================================================
# STOCK
# ============================================================

@app.route("/stock")
def stock():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM blood_stock
        ORDER BY stock_id
    """).fetchall()
    conn.close()

    return render_template_string(STOCK_HTML, stock=rows)


@app.route("/stock/update", methods=["POST"])
def update_stock():
    group = request.form.get("blood_group", "")
    action = request.form.get("action", "")
    units = request.form.get("units", "").strip()

    if group not in BLOOD_GROUPS:
        return redirect(url_for("stock"))

    try:
        units = int(units)
    except ValueError:
        return redirect(url_for("stock"))

    if units <= 0:
        return redirect(url_for("stock"))

    conn = get_db()

    row = conn.execute("""
        SELECT available_units
        FROM blood_stock
        WHERE blood_group=?
    """, (group,)).fetchone()

    if not row:
        conn.close()
        return redirect(url_for("stock"))

    current = row["available_units"]

    if action == "ADD":
        new_value = current + units
    elif action == "REMOVE":
        new_value = current - units
        if new_value < 0:
            conn.close()
            return redirect(url_for("stock"))
    else:
        conn.close()
        return redirect(url_for("stock"))

    conn.execute("""
        UPDATE blood_stock
        SET available_units=?, last_updated=?
        WHERE blood_group=?
    """, (new_value, now(), group))

    conn.execute("""
        INSERT INTO stock_transactions
        (blood_group, action, change_units, balance_after, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        group,
        action,
        units,
        new_value,
        now()
    ))

    conn.commit()
    conn.close()

    log_activity(
        "STOCK",
        group + " stock " +
        ("increased by " if action == "ADD" else "decreased by ") +
        str(units) + " units"
    )

    return redirect(url_for("stock"))


# ============================================================
# REQUESTS
# ============================================================

@app.route("/requests")
def requests_page():
    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM blood_requests
        ORDER BY
            CASE priority
                WHEN 'Emergency' THEN 1
                WHEN 'Urgent' THEN 2
                ELSE 3
            END,
            request_id DESC
    """).fetchall()

    conn.close()

    return render_template_string(REQUESTS_HTML, requests=rows)


@app.route("/requests/add", methods=["POST"])
def add_request():
    patient = request.form.get("patient_name", "").strip()
    age = request.form.get("patient_age", "").strip()
    hospital = request.form.get("hospital_name", "").strip()
    doctor = request.form.get("doctor_name", "").strip()
    group = request.form.get("blood_group", "")
    units = request.form.get("required_units", "").strip()
    priority = request.form.get("priority", "Normal")
    notes = request.form.get("notes", "").strip()

    if not patient or not hospital or group not in BLOOD_GROUPS:
        return redirect(url_for("requests_page"))

    try:
        required = int(units)
    except ValueError:
        return redirect(url_for("requests_page"))

    if required <= 0:
        return redirect(url_for("requests_page"))

    patient_age = None

    if age:
        try:
            patient_age = int(age)
        except ValueError:
            patient_age = None

    code = "REQ-" + datetime.now().strftime("%Y%m%d%H%M%S")

    conn = get_db()

    conn.execute("""
        INSERT INTO blood_requests
        (request_code, patient_name, patient_age, hospital_name,
         doctor_name, blood_group, required_units, fulfilled_units,
         request_date, priority, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'Pending', ?)
    """, (
        code,
        patient,
        patient_age,
        hospital,
        doctor,
        group,
        required,
        now(),
        priority,
        notes
    ))

    conn.commit()
    conn.close()

    log_activity(
        "REQUEST",
        priority + " blood request created for " +
        patient + " - " + group
    )

    return redirect(url_for("requests_page"))


@app.route("/requests/status/<int:request_id>/<status>", methods=["POST"])
def request_status(request_id, status):
    allowed = [
        "Pending",
        "Approved",
        "Rejected"
    ]

    if status not in allowed:
        return redirect(url_for("requests_page"))

    conn = get_db()

    row = conn.execute("""
        SELECT patient_name
        FROM blood_requests
        WHERE request_id=?
    """, (request_id,)).fetchone()

    conn.execute("""
        UPDATE blood_requests
        SET status=?
        WHERE request_id=?
    """, (status, request_id))

    conn.commit()
    conn.close()

    if row:
        log_activity(
            "REQUEST",
            "Request for " + row["patient_name"] +
            " changed to " + status
        )

    return redirect(url_for("requests_page"))


# ============================================================
# DISTRIBUTION
# ============================================================

@app.route("/distribution")
def distribution():
    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM blood_distribution
        ORDER BY distribution_id DESC
    """).fetchall()

    requests = conn.execute("""
        SELECT *
        FROM blood_requests
        WHERE status IN ('Pending','Approved','Partially Fulfilled')
          AND fulfilled_units < required_units
        ORDER BY request_id DESC
    """).fetchall()

    conn.close()

    return render_template_string(
        DISTRIBUTION_HTML,
        distributions=rows,
        requests=requests
    )


@app.route("/distribution/add", methods=["POST"])
def add_distribution():
    request_id = request.form.get("request_id", "").strip()
    issued_by = request.form.get("issued_by", "").strip()
    units = request.form.get("units", "").strip()
    remarks = request.form.get("remarks", "").strip()

    if not request_id or not issued_by:
        return redirect(url_for("distribution"))

    try:
        request_id = int(request_id)
        units = int(units)
    except ValueError:
        return redirect(url_for("distribution"))

    if units <= 0:
        return redirect(url_for("distribution"))

    conn = get_db()

    try:
        req = conn.execute("""
            SELECT *
            FROM blood_requests
            WHERE request_id=?
        """, (request_id,)).fetchone()

        if not req:
            raise ValueError("Request not found")

        remaining = req["required_units"] - req["fulfilled_units"]

        if units > remaining:
            raise ValueError("Units exceed remaining requirement")

        stock = conn.execute("""
            SELECT available_units
            FROM blood_stock
            WHERE blood_group=?
        """, (req["blood_group"],)).fetchone()

        if not stock or stock["available_units"] < units:
            raise ValueError("Insufficient blood stock")

        new_stock = stock["available_units"] - units
        new_fulfilled = req["fulfilled_units"] + units

        if new_fulfilled >= req["required_units"]:
            new_status = "Completed"
        else:
            new_status = "Partially Fulfilled"

        distribution_code = (
            "DST-" + datetime.now().strftime("%Y%m%d%H%M%S")
        )

        conn.execute("""
            UPDATE blood_stock
            SET available_units=?, last_updated=?
            WHERE blood_group=?
        """, (
            new_stock,
            now(),
            req["blood_group"]
        ))

        conn.execute("""
            INSERT INTO stock_transactions
            (blood_group, action, change_units, balance_after, created_at)
            VALUES (?, 'REMOVE', ?, ?, ?)
        """, (
            req["blood_group"],
            units,
            new_stock,
            now()
        ))

        conn.execute("""
            INSERT INTO blood_distribution
            (distribution_code, request_id, patient_name,
             hospital_name, blood_group, units_distributed,
             distribution_date, issued_by, remarks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            distribution_code,
            request_id,
            req["patient_name"],
            req["hospital_name"],
            req["blood_group"],
            units,
            now(),
            issued_by,
            remarks
        ))

        conn.execute("""
            UPDATE blood_requests
            SET fulfilled_units=?, status=?
            WHERE request_id=?
        """, (
            new_fulfilled,
            new_status,
            request_id
        ))

        conn.commit()

    except Exception:
        conn.rollback()
        conn.close()
        return redirect(url_for("distribution"))

    conn.close()

    log_activity(
        "DISTRIBUTION",
        str(units) + " units of " +
        req["blood_group"] +
        " distributed to " +
        req["patient_name"]
    )

    return redirect(url_for("distribution"))


# ============================================================
# CSV EXPORT
# ============================================================

@app.route("/export/<table_name>")
def export_csv(table_name):
    allowed = {
        "donors": "donors",
        "stock": "blood_stock",
        "requests": "blood_requests",
        "distribution": "blood_distribution"
    }

    if table_name not in allowed:
        return "Invalid table", 400

    conn = get_db()

    rows = conn.execute(
        "SELECT * FROM " + allowed[table_name]
    ).fetchall()

    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    if rows:
        writer.writerow(rows[0].keys())

        for row in rows:
            writer.writerow(list(row))

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=" +
                table_name + ".csv"
        }
    )


# ============================================================
# MAIN HTML
# ============================================================

HTML = r'''
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>Blood Bank Management System</title>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>

*{
    box-sizing:border-box;
    margin:0;
    padding:0;
    font-family:Arial,Helvetica,sans-serif;
}

body{
    background:#070b14;
    color:#eef5ff;
    min-height:100vh;
}

.sidebar{
    position:fixed;
    left:0;
    top:0;
    width:250px;
    height:100vh;
    background:#0b111d;
    border-right:1px solid #243044;
    padding:25px 15px;
    z-index:10;
}

.logo{
    text-align:center;
    padding:15px;
    margin-bottom:30px;
}

.logo-icon{
    font-size:45px;
    color:#ff3158;
}

.logo h2{
    margin-top:8px;
    font-size:20px;
}

.logo span{
    color:#7e8ca4;
    font-size:11px;
    letter-spacing:2px;
}

.nav{
    display:flex;
    flex-direction:column;
    gap:8px;
}

.nav a{
    text-decoration:none;
    color:#9daabe;
    padding:14px;
    border-radius:10px;
    transition:.2s;
    font-size:14px;
}

.nav a:hover,
.nav a.active{
    background:#171f30;
    color:#fff;
    box-shadow:0 0 15px rgba(255,49,88,.12);
}

.nav a span{
    margin-right:10px;
}

.main{
    margin-left:250px;
    padding:25px;
}

.topbar{
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin-bottom:25px;
}

.title h1{
    font-size:28px;
}

.title p{
    color:#75839b;
    margin-top:5px;
    font-size:13px;
}

.live{
    display:flex;
    align-items:center;
    gap:8px;
    color:#64f2b5;
    font-size:12px;
}

.dot{
    width:9px;
    height:9px;
    border-radius:50%;
    background:#64f2b5;
    box-shadow:0 0 12px #64f2b5;
}

.cards{
    display:grid;
    grid-template-columns:repeat(6,1fr);
    gap:15px;
    margin-bottom:20px;
}

.card{
    background:linear-gradient(145deg,#101827,#0b111c);
    border:1px solid #202d40;
    border-radius:14px;
    padding:18px;
    min-height:120px;
}

.card .icon{
    font-size:23px;
}

.card .label{
    color:#718097;
    font-size:11px;
    margin-top:13px;
    text-transform:uppercase;
}

.card .value{
    font-size:27px;
    margin-top:4px;
    font-weight:bold;
}

.grid{
    display:grid;
    grid-template-columns:2fr 1fr;
    gap:18px;
    margin-bottom:18px;
}

.panel{
    background:#0d1522;
    border:1px solid #202d40;
    border-radius:15px;
    padding:20px;
}

.panel-title{
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin-bottom:18px;
}

.panel-title h3{
    font-size:16px;
}

.panel-title span{
    color:#718097;
    font-size:11px;
}

.chart-box{
    height:300px;
}

.activity{
    display:flex;
    flex-direction:column;
    gap:12px;
}

.activity-item{
    display:flex;
    gap:12px;
    border-bottom:1px solid #1b2636;
    padding-bottom:12px;
}

.activity-icon{
    width:34px;
    height:34px;
    border-radius:9px;
    background:#182235;
    display:flex;
    align-items:center;
    justify-content:center;
}

.activity-text{
    flex:1;
}

.activity-text p{
    font-size:12px;
    line-height:1.4;
}

.activity-text small{
    color:#65738a;
    font-size:10px;
}

.stock-grid{
    display:grid;
    grid-template-columns:repeat(8,1fr);
    gap:10px;
}

.blood{
    background:#111b2a;
    border:1px solid #27364c;
    border-radius:12px;
    padding:15px 5px;
    text-align:center;
}

.blood .group{
    color:#ff4d70;
    font-size:18px;
    font-weight:bold;
}

.blood .units{
    font-size:22px;
    margin-top:8px;
}

.blood small{
    color:#68778f;
    font-size:9px;
}

.quick{
    display:flex;
    gap:10px;
    flex-wrap:wrap;
}

.btn{
    border:0;
    border-radius:9px;
    padding:11px 15px;
    background:#1a2638;
    color:white;
    cursor:pointer;
    text-decoration:none;
    font-size:12px;
}

.btn:hover{
    background:#24344d;
}

.btn.red{
    background:#d92e50;
}

.btn.green{
    background:#087d62;
}

.btn.blue{
    background:#245d98;
}

@media(max-width:1200px){
    .cards{
        grid-template-columns:repeat(3,1fr);
    }

    .stock-grid{
        grid-template-columns:repeat(4,1fr);
    }
}

@media(max-width:800px){
    .sidebar{
        position:relative;
        width:100%;
        height:auto;
    }

    .main{
        margin-left:0;
    }

    .grid{
        grid-template-columns:1fr;
    }

    .cards{
        grid-template-columns:repeat(2,1fr);
    }
}

</style>
</head>

<body>

<div class="sidebar">

    <div class="logo">
        <div class="logo-icon">🩸</div>
        <h2>BloodBank OS</h2>
        <span>MANAGEMENT SYSTEM</span>
    </div>

    <div class="nav">
        <a class="active" href="/"><span>◈</span> Dashboard</a>
        <a href="/donors"><span>♙</span> Donors</a>
        <a href="/stock"><span>▣</span> Blood Stock</a>
        <a href="/requests"><span>⌁</span> Blood Requests</a>
        <a href="/distribution"><span>⇢</span> Distribution</a>
    </div>

</div>

<div class="main">

    <div class="topbar">
        <div class="title">
            <h1>Blood Bank Dashboard</h1>
            <p>Real-time blood inventory and hospital request monitoring</p>
        </div>

        <div class="live">
            <div class="dot"></div>
            LIVE SYSTEM
        </div>
    </div>


    <div class="cards">

        <div class="card">
            <div class="icon">♙</div>
            <div class="label">Total Donors</div>
            <div class="value" id="donors">0</div>
        </div>

        <div class="card">
            <div class="icon">🩸</div>
            <div class="label">Available Units</div>
            <div class="value" id="units">0</div>
        </div>

        <div class="card">
            <div class="icon">⌁</div>
            <div class="label">Pending Requests</div>
            <div class="value" id="pending">0</div>
        </div>

        <div class="card">
            <div class="icon">⚠</div>
            <div class="label">Emergency</div>
            <div class="value" id="emergency">0</div>
        </div>

        <div class="card">
            <div class="icon">⇢</div>
            <div class="label">Distributions</div>
            <div class="value" id="distributions">0</div>
        </div>

        <div class="card">
            <div class="icon">◉</div>
            <div class="label">Low Stock</div>
            <div class="value" id="lowstock">0</div>
        </div>

    </div>


    <div class="panel" style="margin-bottom:18px;">

        <div class="panel-title">
            <h3>Blood Inventory</h3>
            <span>UNITS AVAILABLE</span>
        </div>

        <div class="stock-grid" id="stockGrid"></div>

    </div>


    <div class="grid">

        <div class="panel">

            <div class="panel-title">
                <h3>Blood Stock Analysis</h3>
                <span>LIVE DATA</span>
            </div>

            <div class="chart-box">
                <canvas id="stockChart"></canvas>
            </div>

        </div>


        <div class="panel">

            <div class="panel-title">
                <h3>Request Status</h3>
                <span>REQUESTS</span>
            </div>

            <div class="chart-box">
                <canvas id="statusChart"></canvas>
            </div>

        </div>

    </div>


    <div class="grid">

        <div class="panel">

            <div class="panel-title">
                <h3>Stock Movement</h3>
                <span>LAST 7 DAYS</span>
            </div>

            <div class="chart-box">
                <canvas id="historyChart"></canvas>
            </div>

        </div>


        <div class="panel">

            <div class="panel-title">
                <h3>Recent Activity</h3>
                <span>AUDIT LOG</span>
            </div>

            <div class="activity" id="activity"></div>

        </div>

    </div>


    <div class="panel">

        <div class="panel-title">
            <h3>Quick Actions</h3>
            <span>SYSTEM OPERATIONS</span>
        </div>

        <div class="quick">

            <a class="btn red" href="/donors">
                + Register Donor
            </a>

            <a class="btn blue" href="/stock">
                + Update Stock
            </a>

            <a class="btn" href="/requests">
                + New Blood Request
            </a>

            <a class="btn green" href="/distribution">
                + Distribute Blood
            </a>

            <a class="btn" href="/export/donors">
                Export Donors
            </a>

            <a class="btn" href="/export/requests">
                Export Requests
            </a>

        </div>

    </div>

</div>


<script>

let stockChart;
let statusChart;
let historyChart;


function createCharts(data){

    const labels = data.stock.map(x => x.group);
    const values = data.stock.map(x => x.units);

    if(stockChart){
        stockChart.destroy();
    }

    stockChart = new Chart(
        document.getElementById("stockChart"),
        {
            type:"bar",
            data:{
                labels:labels,
                datasets:[
                    {
                        label:"Available Units",
                        data:values,
                        borderWidth:1,
                        borderRadius:6
                    }
                ]
            },
            options:{
                responsive:true,
                maintainAspectRatio:false,
                plugins:{
                    legend:{
                        labels:{
                            color:"#9daabe"
                        }
                    }
                },
                scales:{
                    x:{
                        ticks:{
                            color:"#77869d"
                        },
                        grid:{
                            color:"#182333"
                        }
                    },
                    y:{
                        beginAtZero:true,
                        ticks:{
                            color:"#77869d"
                        },
                        grid:{
                            color:"#182333"
                        }
                    }
                }
            }
        }
    );


    const statusLabels =
        data.statuses.map(x => x.status);

    const statusValues =
        data.statuses.map(x => x.total);

    if(statusChart){
        statusChart.destroy();
    }

    statusChart = new Chart(
        document.getElementById("statusChart"),
        {
            type:"doughnut",
            data:{
                labels:statusLabels,
                datasets:[
                    {
                        data:statusValues
                    }
                ]
            },
            options:{
                responsive:true,
                maintainAspectRatio:false,
                plugins:{
                    legend:{
                        position:"bottom",
                        labels:{
                            color:"#9daabe"
                        }
                    }
                }
            }
        }
    );


    const historyLabels =
        data.history.map(x => x.day);

    const added =
        data.history.map(x => x.added);

    const removed =
        data.history.map(x => x.removed);

    if(historyChart){
        historyChart.destroy();
    }

    historyChart = new Chart(
        document.getElementById("historyChart"),
        {
            type:"line",
            data:{
                labels:historyLabels,
                datasets:[
                    {
                        label:"Added",
                        data:added,
                        tension:.35,
                        borderWidth:2
                    },
                    {
                        label:"Removed",
                        data:removed,
                        tension:.35,
                        borderWidth:2
                    }
                ]
            },
            options:{
                responsive:true,
                maintainAspectRatio:false,
                plugins:{
                    legend:{
                        labels:{
                            color:"#9daabe"
                        }
                    }
                },
                scales:{
                    x:{
                        ticks:{
                            color:"#77869d"
                        },
                        grid:{
                            color:"#182333"
                        }
                    },
                    y:{
                        beginAtZero:true,
                        ticks:{
                            color:"#77869d"
                        },
                        grid:{
                            color:"#182333"
                        }
                    }
                }
            }
        }
    );

}


function updatePage(data){

    document.getElementById("donors").textContent =
        data.stats.donors;

    document.getElementById("units").textContent =
        data.stats.units;

    document.getElementById("pending").textContent =
        data.stats.pending;

    document.getElementById("emergency").textContent =
        data.stats.emergency;

    document.getElementById("distributions").textContent =
        data.stats.distributions;

    document.getElementById("lowstock").textContent =
        data.stats.low_stock;


    const grid =
        document.getElementById("stockGrid");

    grid.innerHTML = "";

    data.stock.forEach(item => {

        const div = document.createElement("div");

        div.className = "blood";

        div.innerHTML =
            '<div class="group">' +
            item.group +
            '</div>' +
            '<div class="units">' +
            item.units +
            '</div>' +
            '<small>UNITS</small>';

        grid.appendChild(div);

    });


    const activity =
        document.getElementById("activity");

    activity.innerHTML = "";

    data.activities.forEach(item => {

        const div =
            document.createElement("div");

        div.className =
            "activity-item";

        div.innerHTML =
            '<div class="activity-icon">◉</div>' +
            '<div class="activity-text">' +
            '<p>' + item.message + '</p>' +
            '<small>' + item.time + '</small>' +
            '</div>';

        activity.appendChild(div);

    });


    createCharts(data);
}


async function refresh(){

    try{

        const response =
            await fetch("/api/dashboard");

        const data =
            await response.json();

        updatePage(data);

    }
    catch(error){

        console.log(error);

    }

}


refresh();

setInterval(refresh,5000);

</script>

</body>
</html>
'''


# ============================================================
# DONOR HTML
# ============================================================

DONORS_HTML = r'''
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">

<title>Donor Management</title>

<style>

*{
box-sizing:border-box;
font-family:Arial;
}

body{
margin:0;
background:#070b14;
color:#eef5ff;
}

.header{
padding:25px;
border-bottom:1px solid #202d40;
display:flex;
justify-content:space-between;
align-items:center;
}

.header h1{
margin:0;
}

.header a{
color:white;
text-decoration:none;
background:#202d40;
padding:10px 15px;
border-radius:8px;
}

.container{
padding:25px;
}

.form{
background:#0d1522;
padding:20px;
border:1px solid #202d40;
border-radius:14px;
margin-bottom:25px;
}

.form-grid{
display:grid;
grid-template-columns:repeat(3,1fr);
gap:12px;
}

input,select,textarea{
width:100%;
padding:11px;
background:#101a29;
border:1px solid #2b3b52;
border-radius:8px;
color:white;
outline:none;
}

textarea{
resize:vertical;
}

button{
padding:11px 17px;
border:0;
border-radius:8px;
background:#d92e50;
color:white;
cursor:pointer;
}

table{
width:100%;
border-collapse:collapse;
background:#0d1522;
border-radius:12px;
overflow:hidden;
}

th,td{
padding:13px;
border-bottom:1px solid #1d293a;
text-align:left;
font-size:12px;
}

th{
color:#8190a8;
font-size:11px;
}

.delete{
background:#5b1e2c;
}

@media(max-width:800px){
.form-grid{
grid-template-columns:1fr;
}
}

</style>
</head>

<body>

<div class="header">

<h1>🩸 Donor Management</h1>

<a href="/">← Dashboard</a>

</div>

<div class="container">

<div class="form">

<h3>Register New Donor</h3>

<br>

<form method="POST" action="/donors/add">

<div class="form-grid">

<input name="full_name" placeholder="Full Name" required>

<input name="age" type="number" min="18" max="65" placeholder="Age" required>

<select name="gender">
<option value="">Gender</option>
<option>Male</option>
<option>Female</option>
<option>Other</option>
</select>

<select name="blood_group" required>
<option value="">Blood Group</option>
<option>A+</option>
<option>A-</option>
<option>B+</option>
<option>B-</option>
<option>AB+</option>
<option>AB-</option>
<option>O+</option>
<option>O-</option>
</select>

<input name="phone" placeholder="Phone">

<input name="email" type="email" placeholder="Email">

<input name="address" placeholder="Address">

<input name="last_donation_date" type="date">

</div>

<br>

<button type="submit">+ Register Donor</button>

</form>

</div>


<table>

<tr>
<th>ID</th>
<th>CODE</th>
<th>NAME</th>
<th>AGE</th>
<th>GENDER</th>
<th>BLOOD GROUP</th>
<th>PHONE</th>
<th>REGISTERED</th>
<th>ACTION</th>
</tr>

{% for d in donors %}

<tr>

<td>{{ d["donor_id"] }}</td>

<td>{{ d["donor_code"] }}</td>

<td>{{ d["full_name"] }}</td>

<td>{{ d["age"] }}</td>

<td>{{ d["gender"] or "-" }}</td>

<td><b>{{ d["blood_group"] }}</b></td>

<td>{{ d["phone"] or "-" }}</td>

<td>{{ d["registration_date"] }}</td>

<td>

<form method="POST"
action="/donors/delete/{{ d['donor_id'] }}">

<button class="delete"
onclick="return confirm('Delete this donor?')">

Delete

</button>

</form>

</td>

</tr>

{% endfor %}

</table>

</div>

</body>
</html>
'''


# ============================================================
# STOCK HTML
# ============================================================

STOCK_HTML = r'''
<!DOCTYPE html>
<html>
<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Blood Stock</title>

<style>

*{
box-sizing:border-box;
font-family:Arial;
}

body{
margin:0;
background:#070b14;
color:white;
}

.header{
padding:25px;
border-bottom:1px solid #202d40;
display:flex;
justify-content:space-between;
align-items:center;
}

.header a{
color:white;
background:#202d40;
padding:10px 15px;
border-radius:8px;
text-decoration:none;
}

.container{
padding:25px;
}

.form{
background:#0d1522;
border:1px solid #202d40;
border-radius:14px;
padding:20px;
margin-bottom:25px;
}

.grid{
display:grid;
grid-template-columns:repeat(3,1fr);
gap:12px;
}

input,select{
padding:12px;
background:#101a29;
border:1px solid #2a3a50;
color:white;
border-radius:8px;
}

button{
padding:12px;
border:0;
border-radius:8px;
background:#d92e50;
color:white;
cursor:pointer;
}

.cards{
display:grid;
grid-template-columns:repeat(4,1fr);
gap:15px;
}

.card{
background:#0d1522;
border:1px solid #202d40;
border-radius:14px;
padding:25px;
text-align:center;
}

.group{
font-size:28px;
color:#ff4d70;
font-weight:bold;
}

.units{
font-size:30px;
margin-top:10px;
}

.status{
font-size:11px;
color:#718097;
margin-top:5px;
}

@media(max-width:800px){
.grid,.cards{
grid-template-columns:1fr 1fr;
}
}

</style>

</head>

<body>

<div class="header">

<h1>🩸 Blood Stock</h1>

<a href="/">← Dashboard</a>

</div>

<div class="container">

<div class="form">

<h3>Update Blood Stock</h3>

<br>

<form method="POST" action="/stock/update">

<div class="grid">

<select name="blood_group" required>

<option value="">Blood Group</option>

{% for g in ["A+","A-","B+","B-","AB+","AB-","O+","O-"] %}

<option>{{ g }}</option>

{% endfor %}

</select>

<input
type="number"
name="units"
min="1"
placeholder="Units"
required>

<select name="action">

<option value="ADD">Add Units</option>

<option value="REMOVE">Remove Units</option>

</select>

</div>

<br>

<button>Update Stock</button>

</form>

</div>


<div class="cards">

{% for s in stock %}

<div class="card">

<div class="group">
{{ s["blood_group"] }}
</div>

<div class="units">
{{ s["available_units"] }}
</div>

<div class="status">

{% if s["available_units"] < 5 %}

LOW STOCK

{% elif s["available_units"] < 10 %}

MEDIUM

{% else %}

AVAILABLE

{% endif %}

</div>

</div>

{% endfor %}

</div>

<br>

<a href="/export/stock"
style="color:white;">

Export Stock CSV

</a>

</div>

</body>
</html>
'''


# ============================================================
# REQUEST HTML
# ============================================================

REQUESTS_HTML = r'''
<!DOCTYPE html>
<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Blood Requests</title>

<style>

*{
box-sizing:border-box;
font-family:Arial;
}

body{
margin:0;
background:#070b14;
color:white;
}

.header{
padding:25px;
border-bottom:1px solid #202d40;
display:flex;
justify-content:space-between;
align-items:center;
}

.header a{
background:#202d40;
color:white;
text-decoration:none;
padding:10px 15px;
border-radius:8px;
}

.container{
padding:25px;
}

.form{
background:#0d1522;
border:1px solid #202d40;
border-radius:14px;
padding:20px;
margin-bottom:25px;
}

.grid{
display:grid;
grid-template-columns:repeat(4,1fr);
gap:12px;
}

input,select,textarea{
width:100%;
padding:11px;
background:#101a29;
border:1px solid #2b3b52;
border-radius:8px;
color:white;
}

textarea{
grid-column:span 2;
}

button{
padding:9px 12px;
border:0;
border-radius:7px;
background:#d92e50;
color:white;
cursor:pointer;
margin:2px;
}

.approve{
background:#087d62;
}

.reject{
background:#70243a;
}

table{
width:100%;
border-collapse:collapse;
background:#0d1522;
}

th,td{
padding:12px;
border-bottom:1px solid #1d293a;
font-size:11px;
text-align:left;
}

th{
color:#8190a8;
}

.emergency{
color:#ff486b;
font-weight:bold;
}

.urgent{
color:#ffb74d;
font-weight:bold;
}

.status{
padding:5px 8px;
border-radius:5px;
background:#1c2a3e;
}

@media(max-width:900px){
.grid{
grid-template-columns:1fr 1fr;
}
}

</style>

</head>

<body>

<div class="header">

<h1>⌁ Blood Requests</h1>

<a href="/">← Dashboard</a>

</div>

<div class="container">

<div class="form">

<h3>Create Blood Request</h3>

<br>

<form method="POST" action="/requests/add">

<div class="grid">

<input
name="patient_name"
placeholder="Patient Name"
required>

<input
name="patient_age"
type="number"
placeholder="Patient Age">

<input
name="hospital_name"
placeholder="Hospital Name"
required>

<input
name="doctor_name"
placeholder="Doctor Name">

<select name="blood_group" required>

<option value="">Blood Group</option>

<option>A+</option>
<option>A-</option>
<option>B+</option>
<option>B-</option>
<option>AB+</option>
<option>AB-</option>
<option>O+</option>
<option>O-</option>

</select>

<input
name="required_units"
type="number"
min="1"
placeholder="Required Units"
required>

<select name="priority">

<option>Normal</option>
<option>Urgent</option>
<option>Emergency</option>

</select>

<textarea
name="notes"
placeholder="Additional Notes"></textarea>

</div>

<br>

<button>Create Request</button>

</form>

</div>


<table>

<tr>

<th>CODE</th>
<th>PATIENT</th>
<th>HOSPITAL</th>
<th>BLOOD</th>
<th>REQUIRED</th>
<th>FULFILLED</th>
<th>PRIORITY</th>
<th>STATUS</th>
<th>ACTION</th>

</tr>


{% for r in requests %}

<tr>

<td>{{ r["request_code"] }}</td>

<td>
<b>{{ r["patient_name"] }}</b>
</td>

<td>{{ r["hospital_name"] }}</td>

<td><b>{{ r["blood_group"] }}</b></td>

<td>{{ r["required_units"] }}</td>

<td>{{ r["fulfilled_units"] }}</td>

<td>

{% if r["priority"] == "Emergency" %}

<span class="emergency">EMERGENCY</span>

{% elif r["priority"] == "Urgent" %}

<span class="urgent">URGENT</span>

{% else %}

Normal

{% endif %}

</td>

<td>

<span class="status">
{{ r["status"] }}
</span>

</td>

<td>

{% if r["status"] != "Completed" and r["status"] != "Rejected" %}

<form
style="display:inline"
method="POST"
action="/requests/status/{{ r['request_id'] }}/Approved">

<button class="approve">
Approve
</button>

</form>

<form
style="display:inline"
method="POST"
action="/requests/status/{{ r['request_id'] }}/Rejected">

<button class="reject">
Reject
</button>

</form>

{% endif %}

</td>

</tr>

{% endfor %}

</table>

<br>

<a href="/export/requests"
style="color:white;">

Export Requests CSV

</a>

</div>

</body>

</html>
'''


# ============================================================
# DISTRIBUTION HTML
# ============================================================

DISTRIBUTION_HTML = r'''
<!DOCTYPE html>
<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Blood Distribution</title>

<style>

*{
box-sizing:border-box;
font-family:Arial;
}

body{
margin:0;
background:#070b14;
color:white;
}

.header{
padding:25px;
border-bottom:1px solid #202d40;
display:flex;
justify-content:space-between;
align-items:center;
}

.header a{
color:white;
background:#202d40;
padding:10px 15px;
text-decoration:none;
border-radius:8px;
}

.container{
padding:25px;
}

.form{
background:#0d1522;
border:1px solid #202d40;
padding:20px;
border-radius:14px;
margin-bottom:25px;
}

.grid{
display:grid;
grid-template-columns:repeat(3,1fr);
gap:12px;
}

select,input,textarea{
width:100%;
padding:12px;
background:#101a29;
border:1px solid #2b3b52;
color:white;
border-radius:8px;
}

button{
padding:12px 18px;
border:0;
border-radius:8px;
background:#087d62;
color:white;
cursor:pointer;
}

table{
width:100%;
border-collapse:collapse;
background:#0d1522;
}

th,td{
padding:13px;
border-bottom:1px solid #1d293a;
font-size:11px;
text-align:left;
}

th{
color:#8190a8;
}

</style>

</head>

<body>

<div class="header">

<h1>⇢ Blood Distribution</h1>

<a href="/">← Dashboard</a>

</div>

<div class="container">

<div class="form">

<h3>Issue Blood</h3>

<br>

<form method="POST" action="/distribution/add">

<div class="grid">

<select name="request_id" required>

<option value="">Select Blood Request</option>

{% for r in requests %}

<option value="{{ r['request_id'] }}">

{{ r["request_code"] }} -
{{ r["patient_name"] }} -
{{ r["blood_group"] }} -
Remaining:
{{ r["required_units"] - r["fulfilled_units"] }}

</option>

{% endfor %}

</select>

<input
type="number"
name="units"
min="1"
placeholder="Units to Distribute"
required>

<input
name="issued_by"
placeholder="Issued By"
required>

</div>

<br>

<input
name="remarks"
placeholder="Remarks"
style="width:100%;padding:12px;background:#101a29;border:1px solid #2b3b52;color:white;border-radius:8px;">

<br><br>

<button>
Confirm Distribution
</button>

</form>

</div>


<table>

<tr>

<th>CODE</th>
<th>PATIENT</th>
<th>HOSPITAL</th>
<th>BLOOD</th>
<th>UNITS</th>
<th>DATE</th>
<th>ISSUED BY</th>

</tr>

{% for d in distributions %}

<tr>

<td>{{ d["distribution_code"] }}</td>

<td>{{ d["patient_name"] }}</td>

<td>{{ d["hospital_name"] }}</td>

<td><b>{{ d["blood_group"] }}</b></td>

<td>{{ d["units_distributed"] }}</td>

<td>{{ d["distribution_date"] }}</td>

<td>{{ d["issued_by"] }}</td>

</tr>

{% endfor %}

</table>

<br>

<a
href="/export/distribution"
style="color:white;">

Export Distribution CSV

</a>

</div>

</body>

</html>
'''


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":

    init_db()

    print("")
    print("=" * 60)
    print(" BLOOD BANK MANAGEMENT SYSTEM")
    print("=" * 60)
    print(" Database : " + DB_NAME)
    print(" Server   : http://127.0.0.1:" + str(PORT))
    print(" Dashboard: http://127.0.0.1:" + str(PORT) + "/")
    print("=" * 60)
    print("")

    app.run(
        host="127.0.0.1",
        port=PORT,
        debug=True
    )
