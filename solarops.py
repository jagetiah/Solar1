"""
SolarOps (single-file edition) - Solar Dealership Management App
Run:  streamlit run solarops.py
Same features as the multi-file version, packaged as one file for
zero-friction deployment on Streamlit Community Cloud.
"""


import streamlit as st
import pandas as pd


# ======================================================================
# DATA LAYER
# ======================================================================

"""
database.py — central data layer for SolarOps.

Uses SQLite by default (zero-setup, file-based). For production multi-user
deployments, point DB_URL-style logic at PostgreSQL/Supabase — every module
talks to the DB through these helpers, so swapping the engine is one change.

Two-way sync: the app writes here instantly, and anything written to the DB
by an external process (ERP export, script, admin tool) shows up in the app
on the next interaction/refresh because every page re-queries the DB.
"""

import sqlite3
import hashlib
import os
from datetime import date, datetime

DB_PATH = os.environ.get("SOLAR_DB_PATH", os.path.join(os.path.dirname(__file__), "solar.db"))


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def run(query, params=(), fetch=False):
    """Execute a query. Returns list of dict rows if fetch=True."""
    conn = get_conn()
    try:
        cur = conn.execute(query, params)
        if fetch:
            rows = [dict(r) for r in cur.fetchall()]
            return rows
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def df(query, params=()):
    """Return query results as a pandas DataFrame."""
    import pandas as pd
    conn = get_conn()
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def hash_password(password: str) -> str:
    salt = "solarops-static-salt"  # replace with per-user salt in production
    return hashlib.sha256((salt + password).encode()).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    role TEXT NOT NULL DEFAULT 'sales',   -- admin | manager | sales | inventory | accounts
    active INTEGER DEFAULT 1
);

-- ============ INVENTORY ============
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT,                        -- Panel, Inverter, Battery, Structure, Cable, Kit ...
    unit_price REAL DEFAULT 0,
    reorder_level INTEGER DEFAULT 5
);

-- Serialized stock: every physical unit has a designated ID
CREATE TABLE IF NOT EXISTS inventory_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_id TEXT UNIQUE NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id),
    status TEXT DEFAULT 'in_stock',       -- in_stock | allocated | supplied | damaged | returned
    customer_id INTEGER,                  -- set when supplied
    supplied_date TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT,
    product_id INTEGER REFERENCES products(id),
    quantity INTEGER,
    status TEXT DEFAULT 'placed',         -- placed | in_transit | received | cancelled
    order_date TEXT,
    expected_date TEXT,
    created_by TEXT
);

CREATE TABLE IF NOT EXISTS demand_forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    period TEXT,                          -- e.g. 2026-09
    estimated_qty INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS breakage_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_id TEXT,
    description TEXT,
    claim_amount REAL,
    status TEXT DEFAULT 'reported',       -- reported | claim_filed | approved | paid | rejected
    reported_by TEXT,
    reported_on TEXT
);

-- ============ LEADS ============
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    source TEXT,                          -- Referral, Website, Walk-in, Facebook, Retailer ...
    created_at TEXT,
    first_contact_at TEXT,                -- to compute response speed
    assigned_rep TEXT,
    status TEXT DEFAULT 'new',            -- new | contacted | site_visit | quoted | won | lost
    site_visit_date TEXT,
    quote_amount REAL,
    system_size_kw REAL,
    lost_reason TEXT,
    converted_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS lead_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id),
    activity TEXT,                        -- call, whatsapp, info_shared, quote_sent ...
    detail TEXT,
    at TEXT,
    by_user TEXT
);

-- ============ CUSTOMERS & JOURNEY ============
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id),
    name TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    order_value REAL DEFAULT 0,
    stage TEXT DEFAULT 'order_confirmed',
    -- stages: order_confirmed > invoiced > advance_paid > loan_process >
    --         pre_supply_paid > supplied > installed > meter_testing >
    --         commissioned > closed
    loan_required INTEGER DEFAULT 0,
    loan_status TEXT,                     -- docs_pending | bank_verification | approved | disbursed | na
    subsidy_status TEXT DEFAULT 'not_started',
    warranty_notes TEXT,
    feedback TEXT,
    referral_names TEXT,
    green_kwh_generated REAL DEFAULT 0,   -- for green energy scorecard
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(id),
    invoice_no TEXT,
    amount REAL,
    bill_name TEXT,
    bill_address TEXT,
    created_at TEXT,
    status TEXT DEFAULT 'draft'           -- draft | issued | adjusted | paid
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(id),
    ptype TEXT,                           -- advance | pre_supply | final | partner
    amount REAL,
    paid_on TEXT,
    mode TEXT,                            -- UPI, Bank transfer, Cheque, Cash, Loan disbursal
    status TEXT DEFAULT 'received',       -- due | received | overdue
    due_date TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS service_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(id),
    ttype TEXT,                           -- warranty | subsidy | service | adhoc_query | followup
    description TEXT,
    status TEXT DEFAULT 'open',           -- open | in_progress | resolved
    created_at TEXT,
    resolved_at TEXT,
    handled_by TEXT
);

-- ============ RETAIL PARTNERS ============
CREATE TABLE IF NOT EXISTS retail_partners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name TEXT NOT NULL,
    contact_person TEXT,
    phone TEXT,
    location TEXT,
    source TEXT DEFAULT 'manual',         -- manual | prospect_import | web_research
    status TEXT DEFAULT 'prospect',       -- prospect | contacted | onboarded | inactive
    trained INTEGER DEFAULT 0,
    assigned_rep TEXT,
    notes TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS partner_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partner_id INTEGER REFERENCES retail_partners(id),
    product_id INTEGER REFERENCES products(id),
    quantity INTEGER,
    amount REAL,
    order_date TEXT,
    payment_status TEXT DEFAULT 'due',    -- due | partial | paid
    status TEXT DEFAULT 'placed'          -- placed | supplied | closed
);

-- ============ INCENTIVES ============
CREATE TABLE IF NOT EXISTS incentives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee TEXT,
    ref_type TEXT,                        -- lead_conversion | partner_business
    ref_id INTEGER,                       -- lead id or partner order id
    amount REAL,
    status TEXT DEFAULT 'pending',        -- pending | approved | paid
    created_at TEXT,
    notes TEXT
);
"""


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()

    # Seed a default admin + demo users on first run
    cur = conn.execute("SELECT COUNT(*) c FROM users")
    if cur.fetchone()["c"] == 0:
        demo_users = [
            ("admin", "admin123", "Business Owner", "admin"),
            ("manager", "manager123", "Ops Manager", "manager"),
            ("ravi", "sales123", "Ravi Kumar (Sales)", "sales"),
            ("store", "store123", "Store Keeper", "inventory"),
            ("accounts", "acc123", "Accounts Team", "accounts"),
        ]
        for u, p, n, r in demo_users:
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)",
                (u, hash_password(p), n, r),
            )

        # Seed a few products so the app isn't empty
        products = [
            ("PNL-540", "540W Mono PERC Panel", "Panel", 11500, 20),
            ("INV-5K", "5kW On-grid Inverter", "Inverter", 42000, 5),
            ("STR-3K", "3kW Mounting Structure", "Structure", 9000, 10),
            ("KIT-3K", "3kW Residential Solar Kit", "Kit", 165000, 3),
            ("CBL-DC", "DC Cable 100m Roll", "Cable", 5500, 8),
        ]
        for sku, name, cat, price, rl in products:
            conn.execute(
                "INSERT INTO products (sku, name, category, unit_price, reorder_level) VALUES (?,?,?,?,?)",
                (sku, name, cat, price, rl),
            )
        conn.commit()
    conn.close()


def today():
    return date.today().isoformat()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ======================================================================
# AUTH & RBAC
# ======================================================================

"""auth.py — login, session handling and role-based access control."""

import streamlit as st

# Which roles can see which pages
ROLE_PAGES = {
    "admin":     ["Dashboard", "Inventory", "Leads", "Customer Journey",
                  "Retail Partners", "Payments & Accounting", "Incentives",
                  "Alerts", "User Management"],
    "manager":   ["Dashboard", "Inventory", "Leads", "Customer Journey",
                  "Retail Partners", "Payments & Accounting", "Incentives", "Alerts"],
    "sales":     ["Dashboard", "Leads", "Customer Journey", "Retail Partners"],
    "inventory": ["Dashboard", "Inventory", "Alerts"],
    "accounts":  ["Dashboard", "Payments & Accounting", "Incentives", "Alerts"],
}

ROLE_LABELS = {
    "admin": "Owner / Admin",
    "manager": "Manager",
    "sales": "Sales Rep",
    "inventory": "Inventory Staff",
    "accounts": "Accounts",
}


def login_user(username: str, password: str):
    rows = run(
        "SELECT * FROM users WHERE username=? AND password_hash=? AND active=1",
        (username.strip(), hash_password(password)),
        fetch=True,
    )
    return rows[0] if rows else None


def require_login():
    """Render the login screen if not authenticated. Returns user dict or None."""
    if "user" in st.session_state:
        return st.session_state["user"]

    st.markdown("## ☀️ SolarOps — Partner & Dealership Manager")
    st.caption("Sign in to continue")

    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        ok = st.form_submit_button("Sign in", use_container_width=True)

    if ok:
        user = login_user(u, p)
        if user:
            st.session_state["user"] = user
            st.rerun()
        else:
            st.error("Invalid username or password.")

    with st.expander("Demo accounts"):
        st.markdown(
            "| Role | Username | Password |\n|---|---|---|\n"
            "| Owner/Admin | `admin` | `admin123` |\n"
            "| Manager | `manager` | `manager123` |\n"
            "| Sales Rep | `ravi` | `sales123` |\n"
            "| Inventory | `store` | `store123` |\n"
            "| Accounts | `accounts` | `acc123` |"
        )
    return None


def allowed_pages(role: str):
    return ROLE_PAGES.get(role, ["Dashboard"])


def logout():
    st.session_state.pop("user", None)
    st.rerun()

# ======================================================================
# MODULE: INVENTORY
# ======================================================================

"""Inventory management: purchase orders, serialized stock, kit supply,
demand forecasting with gap suggestions, and breakage/damage claims."""

import streamlit as st
import pandas as pd


def render_inventory(user):
    st.header("📦 Inventory & Demand-Supply")
    tabs = st.tabs([
        "Stock Overview", "Place Order", "Add Stock (Serial IDs)",
        "Supply to Customer", "Demand Forecast & Gaps", "Breakage / Claims",
        "Products",
    ])

    # ---------- Stock overview ----------
    with tabs[0]:
        stock = df("""
            SELECT p.sku, p.name, p.category, p.reorder_level,
                   SUM(CASE WHEN u.status='in_stock' THEN 1 ELSE 0 END) AS in_stock,
                   SUM(CASE WHEN u.status='allocated' THEN 1 ELSE 0 END) AS allocated,
                   SUM(CASE WHEN u.status='supplied' THEN 1 ELSE 0 END) AS supplied,
                   SUM(CASE WHEN u.status='damaged' THEN 1 ELSE 0 END) AS damaged
            FROM products p
            LEFT JOIN inventory_units u ON u.product_id = p.id
            GROUP BY p.id ORDER BY p.name
        """)
        if not stock.empty:
            stock["low_stock"] = stock["in_stock"].fillna(0) < stock["reorder_level"]
            low = stock[stock["low_stock"]]
            if not low.empty:
                st.warning("⚠️ Below reorder level: " + ", ".join(low["name"].tolist()))
        st.dataframe(stock, use_container_width=True, hide_index=True)

        st.subheader("All units (search by serial ID)")
        q = st.text_input("Search serial / status / product")
        units = df("""
            SELECT u.serial_id, p.name AS product, u.status,
                   COALESCE(c.name,'') AS customer, COALESCE(u.supplied_date,'') AS supplied_date
            FROM inventory_units u
            JOIN products p ON p.id = u.product_id
            LEFT JOIN customers c ON c.id = u.customer_id
            ORDER BY u.id DESC
        """)
        if q:
            mask = units.apply(lambda r: q.lower() in " ".join(map(str, r.values)).lower(), axis=1)
            units = units[mask]
        st.dataframe(units, use_container_width=True, hide_index=True)

    # ---------- Purchase orders ----------
    with tabs[1]:
        prods = run("SELECT id, sku || ' — ' || name AS label FROM products ORDER BY name", fetch=True)
        with st.form("po_form", clear_on_submit=True):
            st.subheader("Place a purchase order")
            supplier = st.text_input("Supplier name")
            prod = st.selectbox("Product", prods, format_func=lambda p: p["label"]) if prods else None
            qty = st.number_input("Quantity", 1, 10000, 10)
            exp = st.date_input("Expected delivery date")
            if st.form_submit_button("Place order") and prod:
                run("""INSERT INTO purchase_orders
                       (supplier, product_id, quantity, order_date, expected_date, created_by)
                       VALUES (?,?,?,?,?,?)""",
                    (supplier, prod["id"], qty, today(), exp.isoformat(), user["username"]))
                st.success("Order placed.")

        st.subheader("Open & recent orders")
        pos = df("""SELECT po.id, po.supplier, p.name AS product, po.quantity,
                           po.status, po.order_date, po.expected_date
                    FROM purchase_orders po JOIN products p ON p.id=po.product_id
                    ORDER BY po.id DESC LIMIT 100""")
        st.dataframe(pos, use_container_width=True, hide_index=True)
        open_pos = run("SELECT id FROM purchase_orders WHERE status IN ('placed','in_transit')", fetch=True)
        if open_pos:
            c1, c2 = st.columns(2)
            po_id = c1.selectbox("Order #", [r["id"] for r in open_pos])
            new_status = c2.selectbox("Update status", ["in_transit", "received", "cancelled"])
            if st.button("Update order status"):
                run("UPDATE purchase_orders SET status=? WHERE id=?", (new_status, po_id))
                st.success("Updated. If received, add the units with their serial IDs in the next tab.")
                st.rerun()

    # ---------- Add serialized stock ----------
    with tabs[2]:
        st.subheader("Add received units with designated IDs")
        st.caption("Paste one serial ID per line — each physical unit gets its own trackable ID.")
        prods = run("SELECT id, sku || ' — ' || name AS label FROM products ORDER BY name", fetch=True)
        with st.form("add_units", clear_on_submit=True):
            prod = st.selectbox("Product", prods, format_func=lambda p: p["label"]) if prods else None
            serials = st.text_area("Serial IDs (one per line)", placeholder="PNL-540-0001\nPNL-540-0002")
            if st.form_submit_button("Add to inventory") and prod and serials.strip():
                added, skipped = 0, []
                for s in [x.strip() for x in serials.splitlines() if x.strip()]:
                    try:
                        run("INSERT INTO inventory_units (serial_id, product_id) VALUES (?,?)", (s, prod["id"]))
                        added += 1
                    except Exception:
                        skipped.append(s)
                st.success(f"Added {added} unit(s).")
                if skipped:
                    st.warning(f"Skipped duplicates: {', '.join(skipped)}")

    # ---------- Supply to customer ----------
    with tabs[3]:
        st.subheader("Record a solar kit supply")
        st.caption("Select the customer and the exact serial IDs shipped — inventory updates automatically.")
        custs = run("SELECT id, name FROM customers ORDER BY name", fetch=True)
        in_stock = run("""SELECT u.serial_id, p.name FROM inventory_units u
                          JOIN products p ON p.id=u.product_id
                          WHERE u.status='in_stock' ORDER BY u.serial_id""", fetch=True)
        if not custs:
            st.info("No customers yet — convert a lead in the Leads module first.")
        elif not in_stock:
            st.info("No in-stock units available.")
        else:
            cust = st.selectbox("Customer", custs, format_func=lambda c: c["name"])
            picked = st.multiselect(
                "Units to supply",
                [u["serial_id"] for u in in_stock],
                format_func=lambda s: f"{s} ({next(u['name'] for u in in_stock if u['serial_id']==s)})",
            )
            if st.button("Confirm supply", type="primary", disabled=not picked):
                for s in picked:
                    run("""UPDATE inventory_units SET status='supplied', customer_id=?, supplied_date=?
                           WHERE serial_id=?""", (cust["id"], today(), s))
                run("UPDATE customers SET stage='supplied' WHERE id=? AND stage NOT IN ('installed','commissioned','closed')",
                    (cust["id"],))
                st.success(f"Supplied {len(picked)} unit(s) to {cust['name']}. Inventory updated.")
                st.rerun()

        st.subheader("Supply history")
        hist = df("""SELECT u.serial_id, p.name AS product, c.name AS customer, u.supplied_date
                     FROM inventory_units u JOIN products p ON p.id=u.product_id
                     JOIN customers c ON c.id=u.customer_id
                     WHERE u.status='supplied' ORDER BY u.supplied_date DESC""")
        st.dataframe(hist, use_container_width=True, hide_index=True)

    # ---------- Demand forecast & gaps ----------
    with tabs[4]:
        st.subheader("Feed in future order estimates")
        prods = run("SELECT id, sku || ' — ' || name AS label FROM products ORDER BY name", fetch=True)
        with st.form("forecast", clear_on_submit=True):
            prod = st.selectbox("Product", prods, format_func=lambda p: p["label"]) if prods else None
            period = st.text_input("Period (YYYY-MM)", value=today()[:7])
            qty = st.number_input("Estimated demand (units)", 0, 100000, 10)
            if st.form_submit_button("Save estimate") and prod:
                run("INSERT INTO demand_forecasts (product_id, period, estimated_qty) VALUES (?,?,?)",
                    (prod["id"], period, qty))
                st.success("Estimate saved.")

        st.subheader("Suggested inventory gaps")
        gaps = df("""
            SELECT p.name AS product,
                   SUM(f.estimated_qty) AS forecast_demand,
                   (SELECT COUNT(*) FROM inventory_units u
                     WHERE u.product_id=p.id AND u.status='in_stock') AS in_stock,
                   COALESCE((SELECT SUM(po.quantity) FROM purchase_orders po
                     WHERE po.product_id=p.id AND po.status IN ('placed','in_transit')),0) AS incoming
            FROM demand_forecasts f JOIN products p ON p.id=f.product_id
            GROUP BY p.id
        """)
        if gaps.empty:
            st.info("Add demand estimates above to see gap suggestions.")
        else:
            gaps["gap"] = gaps["forecast_demand"] - gaps["in_stock"] - gaps["incoming"]
            gaps["suggestion"] = gaps["gap"].apply(
                lambda g: f"Order {int(g)} more" if g > 0 else "Sufficient stock")
            st.dataframe(gaps, use_container_width=True, hide_index=True)
            need = gaps[gaps["gap"] > 0]
            if not need.empty:
                st.error("🛒 Reorder needed: " +
                         ", ".join(f"{r.product} (+{int(r.gap)})" for r in need.itertuples()))

    # ---------- Breakage / claims ----------
    with tabs[5]:
        st.subheader("Report breakage")
        units = run("SELECT serial_id FROM inventory_units WHERE status != 'damaged' ORDER BY serial_id", fetch=True)
        with st.form("breakage", clear_on_submit=True):
            serial = st.selectbox("Serial ID", [u["serial_id"] for u in units]) if units else None
            desc = st.text_area("What happened?")
            amt = st.number_input("Claim amount (₹)", 0.0, step=500.0)
            if st.form_submit_button("Report & create claim") and serial:
                run("UPDATE inventory_units SET status='damaged' WHERE serial_id=?", (serial,))
                run("""INSERT INTO breakage_claims (serial_id, description, claim_amount, reported_by, reported_on)
                       VALUES (?,?,?,?,?)""", (serial, desc, amt, user["username"], today()))
                st.success("Breakage recorded and claim created.")

        st.subheader("Damage compensation claims")
        claims = df("SELECT * FROM breakage_claims ORDER BY id DESC")
        st.dataframe(claims, use_container_width=True, hide_index=True)
        open_claims = run("SELECT id FROM breakage_claims WHERE status NOT IN ('paid','rejected')", fetch=True)
        if open_claims and user["role"] in ("admin", "manager", "accounts"):
            c1, c2 = st.columns(2)
            cid = c1.selectbox("Claim #", [r["id"] for r in open_claims])
            ns = c2.selectbox("Move to", ["claim_filed", "approved", "paid", "rejected"])
            if st.button("Update claim"):
                run("UPDATE breakage_claims SET status=? WHERE id=?", (ns, cid))
                st.rerun()

    # ---------- Product master ----------
    with tabs[6]:
        st.subheader("Product master")
        if user["role"] in ("admin", "manager", "inventory"):
            with st.form("newprod", clear_on_submit=True):
                c1, c2 = st.columns(2)
                sku = c1.text_input("SKU / Product ID")
                name = c2.text_input("Product name")
                c3, c4, c5 = st.columns(3)
                cat = c3.text_input("Category")
                price = c4.number_input("Unit price (₹)", 0.0, step=100.0)
                rl = c5.number_input("Reorder level", 0, 1000, 5)
                if st.form_submit_button("Add product") and sku and name:
                    try:
                        run("INSERT INTO products (sku, name, category, unit_price, reorder_level) VALUES (?,?,?,?,?)",
                            (sku, name, cat, price, rl))
                        st.success("Product added.")
                    except Exception:
                        st.error("SKU already exists.")
        st.dataframe(df("SELECT sku, name, category, unit_price, reorder_level FROM products ORDER BY name"),
                     use_container_width=True, hide_index=True)

# ======================================================================
# MODULE: LEADS
# ======================================================================

"""Lead management: capture with source, response speed, rep assignment,
site visits, info sharing, quotations, conversion %, loss reasons, incentives."""

import streamlit as st
import pandas as pd
from datetime import datetime

SOURCES = ["Referral", "Website", "Walk-in", "Facebook/Instagram", "Google", "Retailer", "Exhibition", "Other"]
LOST_REASONS = ["Price too high", "Chose competitor", "Financing not approved",
                "Site not feasible", "Postponed decision", "Not reachable", "Other"]
INCENTIVE_RATE = 0.01  # 1% of quote value on conversion — adjust to your policy


def _reps():
    return [r["username"] for r in run(
        "SELECT username FROM users WHERE role IN ('sales','manager') AND active=1", fetch=True)]


def render_leads(user):
    st.header("🎯 Leads")
    tabs = st.tabs(["Pipeline", "New Lead", "Work a Lead", "Analytics"])

    # ---------- Pipeline ----------
    with tabs[0]:
        leads = df("""SELECT id, name, phone, source, status, assigned_rep,
                             created_at, first_contact_at, site_visit_date,
                             quote_amount, lost_reason
                      FROM leads ORDER BY id DESC""")
        # Sales reps see only their own leads
        if user["role"] == "sales" and not leads.empty:
            leads = leads[leads["assigned_rep"] == user["username"]]
        f = st.multiselect("Filter status", ["new", "contacted", "site_visit", "quoted", "won", "lost"])
        if f and not leads.empty:
            leads = leads[leads["status"].isin(f)]
        st.dataframe(leads, use_container_width=True, hide_index=True)

    # ---------- New lead ----------
    with tabs[1]:
        with st.form("newlead", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Name *")
            phone = c2.text_input("Phone")
            c3, c4 = st.columns(2)
            source = c3.selectbox("Where did this lead come from? *", SOURCES)
            rep = c4.selectbox("Assign to sales rep", _reps() or ["(none)"])
            kw = st.number_input("Approx system size (kW)", 0.0, step=0.5)
            notes = st.text_area("Notes")
            if st.form_submit_button("Add lead", type="primary") and name:
                run("""INSERT INTO leads (name, phone, source, created_at, assigned_rep,
                                          system_size_kw, notes)
                       VALUES (?,?,?,?,?,?,?)""",
                    (name, phone, source, now(), rep, kw, notes))
                st.success(f"Lead added and assigned to {rep}.")

    # ---------- Work a lead ----------
    with tabs[2]:
        open_leads = run(
            "SELECT id, name, status FROM leads WHERE status NOT IN ('won','lost') ORDER BY id DESC",
            fetch=True)
        if user["role"] == "sales":
            open_leads = [l for l in open_leads if True]  # reps can act on assigned leads
        if not open_leads:
            st.info("No open leads.")
        else:
            lead = st.selectbox("Select lead", open_leads,
                                format_func=lambda l: f"#{l['id']} {l['name']} ({l['status']})")
            L = run("SELECT * FROM leads WHERE id=?", (lead["id"],), fetch=True)[0]

            c1, c2, c3 = st.columns(3)
            c1.metric("Status", L["status"])
            c2.metric("Source", L["source"] or "—")
            if L["first_contact_at"] and L["created_at"]:
                try:
                    delta = (datetime.fromisoformat(L["first_contact_at"]) -
                             datetime.fromisoformat(L["created_at"]))
                    c3.metric("Response time", f"{delta.total_seconds()/3600:.1f} hrs")
                except Exception:
                    pass

            act_col, prog_col = st.columns(2)

            with act_col:
                st.subheader("Log activity")
                with st.form("activity", clear_on_submit=True):
                    activity = st.selectbox("Activity", ["First contact (call)", "Call", "WhatsApp",
                                                         "Shared brochure/info", "Shared subsidy details",
                                                         "Sent quotation", "Meeting"])
                    detail = st.text_input("Details / info shared")
                    if st.form_submit_button("Log"):
                        run("INSERT INTO lead_activities (lead_id, activity, detail, at, by_user) VALUES (?,?,?,?,?)",
                            (L["id"], activity, detail, now(), user["username"]))
                        if not L["first_contact_at"]:
                            run("UPDATE leads SET first_contact_at=?, status='contacted' WHERE id=?",
                                (now(), L["id"]))
                        st.rerun()

                acts = df("SELECT at, activity, detail, by_user FROM lead_activities WHERE lead_id=? ORDER BY id DESC",
                          (L["id"],))
                st.dataframe(acts, use_container_width=True, hide_index=True)

            with prog_col:
                st.subheader("Progress the lead")
                sv = st.date_input("Site visit date", key=f"sv{L['id']}")
                if st.button("Mark site visit done"):
                    run("UPDATE leads SET site_visit_date=?, status='site_visit' WHERE id=?",
                        (sv.isoformat(), L["id"]))
                    st.rerun()

                q = st.number_input("Quotation amount (₹)", 0.0, step=5000.0,
                                    value=float(L["quote_amount"] or 0))
                if st.button("Send / update quote"):
                    run("UPDATE leads SET quote_amount=?, status='quoted' WHERE id=?", (q, L["id"]))
                    run("INSERT INTO lead_activities (lead_id, activity, detail, at, by_user) VALUES (?,?,?,?,?)",
                        (L["id"], "Sent quotation", f"₹{q:,.0f}", now(), user["username"]))
                    st.rerun()

                st.divider()
                colw, coll = st.columns(2)
                if colw.button("✅ Mark WON", type="primary"):
                    run("UPDATE leads SET status='won', converted_at=? WHERE id=?", (now(), L["id"]))
                    # Create customer record automatically
                    run("""INSERT INTO customers (lead_id, name, phone, order_value, created_at)
                           VALUES (?,?,?,?,?)""",
                        (L["id"], L["name"], L["phone"], L["quote_amount"] or 0, today()))
                    # Auto-create incentive for the assigned rep
                    if L["assigned_rep"] and (L["quote_amount"] or 0) > 0:
                        amt = round(L["quote_amount"] * INCENTIVE_RATE, 2)
                        run("""INSERT INTO incentives (employee, ref_type, ref_id, amount, created_at, notes)
                               VALUES (?,?,?,?,?,?)""",
                            (L["assigned_rep"], "lead_conversion", L["id"], amt, today(),
                             f"1% of ₹{L['quote_amount']:,.0f} for lead #{L['id']}"))
                    st.success("Converted! Customer created; incentive queued for approval.")
                    st.rerun()

                reason = coll.selectbox("Lost reason", LOST_REASONS, key=f"lr{L['id']}")
                if coll.button("❌ Mark LOST"):
                    run("UPDATE leads SET status='lost', lost_reason=? WHERE id=?", (reason, L["id"]))
                    st.rerun()

    # ---------- Analytics ----------
    with tabs[3]:
        all_leads = df("SELECT * FROM leads")
        if all_leads.empty:
            st.info("No leads yet.")
            return
        total = len(all_leads)
        won = (all_leads["status"] == "won").sum()
        lost = (all_leads["status"] == "lost").sum()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total leads", total)
        c2.metric("Converted", int(won))
        c3.metric("Conversion %", f"{(won/total*100):.1f}%" if total else "0%")
        closed = won + lost
        c4.metric("Win rate (closed)", f"{(won/closed*100):.1f}%" if closed else "—")

        cA, cB = st.columns(2)
        with cA:
            st.subheader("Leads by source")
            st.bar_chart(all_leads["source"].value_counts())
            st.subheader("Conversion % by source")
            conv = all_leads.groupby("source").apply(
                lambda g: (g["status"] == "won").mean() * 100).round(1)
            st.bar_chart(conv)
        with cB:
            st.subheader("Why leads didn't convert")
            lost_df = all_leads[all_leads["status"] == "lost"]
            if lost_df.empty:
                st.caption("No lost leads yet.")
            else:
                st.bar_chart(lost_df["lost_reason"].value_counts())
            st.subheader("Leads per rep")
            st.bar_chart(all_leads["assigned_rep"].value_counts())

        st.subheader("Response speed (lead created → first contact)")
        speed = all_leads.dropna(subset=["first_contact_at"]).copy()
        if not speed.empty:
            speed["hrs"] = (pd.to_datetime(speed["first_contact_at"]) -
                            pd.to_datetime(speed["created_at"])).dt.total_seconds() / 3600
            st.metric("Average response time", f"{speed['hrs'].mean():.1f} hrs")
            st.dataframe(speed[["name", "source", "assigned_rep", "hrs"]].round(1),
                         use_container_width=True, hide_index=True)
        else:
            st.caption("Log a 'First contact' activity on a lead to start tracking response speed.")

# ======================================================================
# MODULE: CUSTOMERS
# ======================================================================

"""Customer journey: order confirmation → invoice → payments → loan →
supply → installation → meter/commissioning → warranty/subsidy →
feedback/referrals → service & green scorecard → follow-ups & adhoc queries."""

import streamlit as st
import pandas as pd

STAGES = ["order_confirmed", "invoiced", "advance_paid", "loan_process",
          "pre_supply_paid", "supplied", "installed", "meter_testing",
          "commissioned", "closed"]

STAGE_LABELS = {
    "order_confirmed": "1. Order confirmed",
    "invoiced": "2. Invoice created",
    "advance_paid": "3. Advance paid (~30%)",
    "loan_process": "4. Loan in process",
    "pre_supply_paid": "5. 60–90% paid",
    "supplied": "6. Kit supplied",
    "installed": "7. Installed",
    "meter_testing": "8. Meter testing / electricity dept",
    "commissioned": "9. Commissioned",
    "closed": "10. Closed",
}


def _paid(cid):
    r = run("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE customer_id=? AND status='received'",
            (cid,), fetch=True)
    return r[0]["s"] or 0


def render_customers(user):
    st.header("🧑‍🤝‍🧑 Customer Journey")
    custs = run("SELECT id, name, stage FROM customers ORDER BY id DESC", fetch=True)
    if not custs:
        st.info("No customers yet. Convert a lead (Leads → Work a Lead → Mark WON) to create one.")
        return

    tabs = st.tabs(["All Customers", "Manage a Customer", "Tickets & Follow-ups"])

    # ---------- Overview ----------
    with tabs[0]:
        data = df("""SELECT c.id, c.name, c.phone, c.stage, c.order_value,
                            c.loan_status, c.subsidy_status, c.created_at
                     FROM customers c ORDER BY c.id DESC""")
        pays = df("SELECT customer_id, SUM(amount) paid FROM payments WHERE status='received' GROUP BY customer_id")
        if not pays.empty:
            data = data.merge(pays, left_on="id", right_on="customer_id", how="left").drop(columns=["customer_id"])
            data["paid"] = data["paid"].fillna(0)
            data["balance"] = data["order_value"] - data["paid"]
        data["stage"] = data["stage"].map(STAGE_LABELS).fillna(data["stage"])
        st.dataframe(data, use_container_width=True, hide_index=True)
        st.subheader("Customers by stage")
        st.bar_chart(data["stage"].value_counts())

    # ---------- Manage a customer ----------
    with tabs[1]:
        cust = st.selectbox("Customer", custs, format_func=lambda c: f"#{c['id']} {c['name']}")
        C = run("SELECT * FROM customers WHERE id=?", (cust["id"],), fetch=True)[0]
        paid = _paid(C["id"])
        bal = (C["order_value"] or 0) - paid

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Stage", STAGE_LABELS.get(C["stage"], C["stage"]))
        m2.metric("Order value", f"₹{C['order_value']:,.0f}")
        m3.metric("Received", f"₹{paid:,.0f}")
        m4.metric("Balance", f"₹{bal:,.0f}")
        st.progress((STAGES.index(C["stage"]) + 1) / len(STAGES))

        s1, s2 = st.tabs(["Order, Invoice & Payments", "Loan, Supply & Post-sale"])

        # --- Order / invoice / payments ---
        with s1:
            st.subheader("Order confirmation sheet")
            with st.form(f"conf{C['id']}"):
                ov = st.number_input("Confirmed order value (₹)", 0.0, step=5000.0,
                                     value=float(C["order_value"] or 0))
                addr = st.text_area("Installation address", value=C["address"] or "")
                if st.form_submit_button("Save confirmation"):
                    run("UPDATE customers SET order_value=?, address=? WHERE id=?", (ov, addr, C["id"]))
                    st.success("Order confirmation saved.")
                    st.rerun()

            st.subheader("Invoice")
            invs = df("SELECT invoice_no, amount, bill_name, bill_address, status, created_at FROM invoices WHERE customer_id=?",
                      (C["id"],))
            st.dataframe(invs, use_container_width=True, hide_index=True)
            with st.form(f"inv{C['id']}", clear_on_submit=True):
                c1, c2 = st.columns(2)
                inv_no = c1.text_input("Invoice no.", value=f"INV-{today().replace('-','')}-{C['id']}")
                amt = c2.number_input("Amount (₹)", 0.0, value=float(C["order_value"] or 0))
                bn = st.text_input("Bill name (adjust if needed)", value=C["name"])
                ba = st.text_input("Bill address (adjust if needed)", value=C["address"] or "")
                if st.form_submit_button("Create / adjust invoice"):
                    run("""INSERT INTO invoices (customer_id, invoice_no, amount, bill_name, bill_address, created_at, status)
                           VALUES (?,?,?,?,?,?,'issued')""", (C["id"], inv_no, amt, bn, ba, today()))
                    if STAGES.index(C["stage"]) < STAGES.index("invoiced"):
                        run("UPDATE customers SET stage='invoiced' WHERE id=?", (C["id"],))
                    st.success("Invoice saved.")
                    st.rerun()

            st.subheader("Record payment")
            with st.form(f"pay{C['id']}", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                ptype = c1.selectbox("Type", ["advance", "pre_supply", "final"])
                default_amt = {"advance": 0.30, "pre_supply": 0.60, "final": 0.10}[ptype] * (C["order_value"] or 0)
                amount = c2.number_input("Amount (₹)", 0.0, value=round(default_amt, 0))
                mode = c3.selectbox("Mode", ["UPI", "Bank transfer", "Cheque", "Cash", "Loan disbursal"])
                if st.form_submit_button("Record payment", type="primary"):
                    run("""INSERT INTO payments (customer_id, ptype, amount, paid_on, mode, status)
                           VALUES (?,?,?,?,?,'received')""", (C["id"], ptype, amount, today(), mode))
                    stage_after = {"advance": "advance_paid", "pre_supply": "pre_supply_paid"}.get(ptype)
                    if stage_after and STAGES.index(C["stage"]) < STAGES.index(stage_after):
                        run("UPDATE customers SET stage=? WHERE id=?", (stage_after, C["id"]))
                    st.success("Payment recorded.")
                    st.rerun()
            pay_hist = df("SELECT ptype, amount, paid_on, mode, status FROM payments WHERE customer_id=? ORDER BY id DESC",
                          (C["id"],))
            st.dataframe(pay_hist, use_container_width=True, hide_index=True)

        # --- Loan / supply / post-sale ---
        with s2:
            st.subheader("Loan process")
            c1, c2 = st.columns(2)
            lr = c1.toggle("Loan required?", value=bool(C["loan_required"]))
            ls = c2.selectbox("Loan status",
                              ["na", "docs_pending", "bank_verification", "approved", "disbursed"],
                              index=["na", "docs_pending", "bank_verification", "approved", "disbursed"]
                              .index(C["loan_status"] or "na"))
            if st.button("Save loan status"):
                run("UPDATE customers SET loan_required=?, loan_status=? WHERE id=?",
                    (int(lr), ls, C["id"]))
                if lr and ls in ("docs_pending", "bank_verification") and \
                   STAGES.index(C["stage"]) < STAGES.index("loan_process"):
                    run("UPDATE customers SET stage='loan_process' WHERE id=?", (C["id"],))
                st.rerun()

            st.subheader("Supplied units")
            units = df("""SELECT u.serial_id, p.name product, u.supplied_date
                          FROM inventory_units u JOIN products p ON p.id=u.product_id
                          WHERE u.customer_id=?""", (C["id"],))
            if units.empty:
                st.caption("No units supplied yet — use Inventory → Supply to Customer.")
            else:
                st.dataframe(units, use_container_width=True, hide_index=True)

            st.subheader("Installation, meter & commissioning")
            nxt = st.selectbox("Move stage to", [STAGE_LABELS[s] for s in
                               ["installed", "meter_testing", "commissioned", "closed"]])
            if st.button("Update stage"):
                key = [k for k, v in STAGE_LABELS.items() if v == nxt][0]
                run("UPDATE customers SET stage=? WHERE id=?", (key, C["id"]))
                st.rerun()

            st.subheader("Subsidy claim")
            ss = st.selectbox("Subsidy status",
                              ["not_started", "applied", "inspection_done", "approved", "credited"],
                              index=["not_started", "applied", "inspection_done", "approved", "credited"]
                              .index(C["subsidy_status"] or "not_started"))
            if st.button("Save subsidy status"):
                run("UPDATE customers SET subsidy_status=? WHERE id=?", (ss, C["id"]))
                st.rerun()

            st.subheader("Feedback, referrals & green scorecard")
            with st.form(f"fb{C['id']}"):
                fb = st.text_area("Customer feedback (for digital marketing)", value=C["feedback"] or "")
                refs = st.text_input("Referrals given (names/phones)", value=C["referral_names"] or "")
                kwh = st.number_input("Green energy generated so far (kWh)", 0.0,
                                      value=float(C["green_kwh_generated"] or 0))
                if st.form_submit_button("Save"):
                    run("""UPDATE customers SET feedback=?, referral_names=?, green_kwh_generated=?
                           WHERE id=?""", (fb, refs, kwh, C["id"]))
                    st.success("Saved.")
                    st.rerun()
            if C["green_kwh_generated"]:
                co2 = C["green_kwh_generated"] * 0.82 / 1000  # ~0.82 kg CO2 per kWh (India grid avg)
                st.success(f"🌱 Scorecard: {C['green_kwh_generated']:,.0f} kWh clean energy ≈ "
                           f"{co2:,.2f} tonnes CO₂ avoided")

    # ---------- Tickets ----------
    with tabs[2]:
        st.subheader("Raise ticket / follow-up / adhoc query")
        with st.form("ticket", clear_on_submit=True):
            cust2 = st.selectbox("Customer ", custs, format_func=lambda c: c["name"])
            tt = st.selectbox("Type", ["warranty", "service", "adhoc_query", "followup", "subsidy"])
            desc = st.text_area("Description")
            if st.form_submit_button("Create ticket"):
                run("""INSERT INTO service_tickets (customer_id, ttype, description, created_at, handled_by)
                       VALUES (?,?,?,?,?)""", (cust2["id"], tt, desc, now(), user["username"]))
                st.success("Ticket created.")

        tickets = df("""SELECT t.id, c.name customer, t.ttype, t.description, t.status,
                               t.created_at, t.handled_by
                        FROM service_tickets t JOIN customers c ON c.id=t.customer_id
                        ORDER BY t.id DESC""")
        st.dataframe(tickets, use_container_width=True, hide_index=True)
        open_t = run("SELECT id FROM service_tickets WHERE status!='resolved'", fetch=True)
        if open_t:
            c1, c2 = st.columns(2)
            tid = c1.selectbox("Ticket #", [t["id"] for t in open_t])
            ns = c2.selectbox("Set status", ["in_progress", "resolved"])
            if st.button("Update ticket"):
                run("UPDATE service_tickets SET status=?, resolved_at=? WHERE id=?",
                    (ns, now() if ns == "resolved" else None, tid))
                st.rerun()

# ======================================================================
# MODULE: PARTNERS
# ======================================================================

"""Retail partners: prospect discovery (CSV import / manual research capture),
onboarding, inventory orders, payments, training, and rep incentives."""

import streamlit as st
import pandas as pd

PARTNER_INCENTIVE_RATE = 0.005  # 0.5% of partner order value to the driving rep


def _partner_reps():
    return [r["username"] for r in run(
        "SELECT username FROM users WHERE role IN ('sales','manager') AND active=1", fetch=True)]


def render_partners(user):
    st.header("🏪 Retail Partners")
    tabs = st.tabs(["Partners", "Find Prospects", "Partner Orders & Payments", "Training"])

    # ---------- Partner list ----------
    with tabs[0]:
        with st.expander("➕ Add partner manually"):
            with st.form("newpartner", clear_on_submit=True):
                c1, c2 = st.columns(2)
                bn = c1.text_input("Business name *")
                cp = c2.text_input("Contact person")
                c3, c4, c5 = st.columns(3)
                ph = c3.text_input("Phone")
                loc = c4.text_input("Location")
                rep = c5.selectbox("Assigned rep", _partner_reps() or ["(none)"])
                if st.form_submit_button("Add") and bn:
                    run("""INSERT INTO retail_partners
                           (business_name, contact_person, phone, location, assigned_rep, created_at)
                           VALUES (?,?,?,?,?,?)""", (bn, cp, ph, loc, rep, today()))
                    st.success("Partner added.")

        partners = df("""SELECT id, business_name, contact_person, phone, location,
                                source, status, trained, assigned_rep
                         FROM retail_partners ORDER BY id DESC""")
        st.dataframe(partners, use_container_width=True, hide_index=True)

        rows = run("SELECT id, business_name FROM retail_partners", fetch=True)
        if rows:
            c1, c2 = st.columns(2)
            pid = c1.selectbox("Partner", rows, format_func=lambda r: r["business_name"])
            ns = c2.selectbox("Set status", ["prospect", "contacted", "onboarded", "inactive"])
            if st.button("Update partner status"):
                run("UPDATE retail_partners SET status=? WHERE id=?", (ns, pid["id"]))
                st.rerun()

    # ---------- Prospects ----------
    with tabs[1]:
        st.subheader("Import prospect list (CSV)")
        st.caption(
            "Bring in potential small businesses (electrical shops, hardware stores, contractors) "
            "from any research source. Expected columns: business_name, contact_person, phone, location. "
            "Note: if you scrape directories for prospects, check each site's terms of service and "
            "local data-protection rules first — many directories prohibit automated scraping, so an "
            "exported/purchased list or manual research is often the safer route."
        )
        up = st.file_uploader("Upload CSV", type=["csv"])
        if up is not None:
            try:
                pdf = pd.read_csv(up)
                st.dataframe(pdf.head(20), use_container_width=True)
                if st.button("Import as prospects", type="primary"):
                    n = 0
                    for _, r in pdf.iterrows():
                        run("""INSERT INTO retail_partners
                               (business_name, contact_person, phone, location, source, created_at)
                               VALUES (?,?,?,?,?,?)""",
                            (str(r.get("business_name", "")), str(r.get("contact_person", "")),
                             str(r.get("phone", "")), str(r.get("location", "")),
                             "prospect_import", today()))
                        n += 1
                    st.success(f"Imported {n} prospects.")
            except Exception as e:
                st.error(f"Could not read CSV: {e}")

        st.subheader("Capture a researched prospect")
        with st.form("research", clear_on_submit=True):
            c1, c2 = st.columns(2)
            bn = c1.text_input("Business name")
            loc = c2.text_input("Location")
            notes = st.text_input("Research notes (e.g., found on local directory, shop type)")
            if st.form_submit_button("Save prospect") and bn:
                run("""INSERT INTO retail_partners (business_name, location, source, notes, created_at)
                       VALUES (?,?, 'web_research', ?, ?)""", (bn, loc, notes, today()))
                st.success("Prospect saved.")

    # ---------- Orders & payments ----------
    with tabs[2]:
        onboarded = run("SELECT id, business_name, assigned_rep FROM retail_partners WHERE status='onboarded'",
                        fetch=True)
        prods = run("SELECT id, sku || ' — ' || name AS label, unit_price FROM products", fetch=True)
        if not onboarded:
            st.info("No onboarded partners yet — set a partner's status to 'onboarded' first.")
        else:
            with st.form("porder", clear_on_submit=True):
                st.subheader("New partner order")
                c1, c2, c3 = st.columns(3)
                p = c1.selectbox("Partner", onboarded, format_func=lambda r: r["business_name"])
                pr = c2.selectbox("Product", prods, format_func=lambda r: r["label"])
                qty = c3.number_input("Qty", 1, 10000, 5)
                if st.form_submit_button("Create order", type="primary"):
                    amt = qty * (pr["unit_price"] or 0)
                    oid = run("""INSERT INTO partner_orders (partner_id, product_id, quantity, amount, order_date)
                                 VALUES (?,?,?,?,?)""", (p["id"], pr["id"], qty, amt, today()))
                    # incentive for the rep driving this partner
                    if p["assigned_rep"]:
                        run("""INSERT INTO incentives (employee, ref_type, ref_id, amount, created_at, notes)
                               VALUES (?,?,?,?,?,?)""",
                            (p["assigned_rep"], "partner_business", oid,
                             round(amt * PARTNER_INCENTIVE_RATE, 2), today(),
                             f"0.5% of ₹{amt:,.0f} partner order #{oid}"))
                    st.success(f"Order created (₹{amt:,.0f}). Rep incentive queued.")

        orders = df("""SELECT o.id, rp.business_name partner, p.name product, o.quantity,
                              o.amount, o.order_date, o.status, o.payment_status
                       FROM partner_orders o
                       JOIN retail_partners rp ON rp.id=o.partner_id
                       JOIN products p ON p.id=o.product_id
                       ORDER BY o.id DESC""")
        st.dataframe(orders, use_container_width=True, hide_index=True)
        oo = run("SELECT id FROM partner_orders WHERE status!='closed' OR payment_status!='paid'", fetch=True)
        if oo:
            c1, c2, c3 = st.columns(3)
            oid = c1.selectbox("Order #", [o["id"] for o in oo])
            os_ = c2.selectbox("Order status", ["placed", "supplied", "closed"])
            ps = c3.selectbox("Payment status", ["due", "partial", "paid"])
            if st.button("Update partner order"):
                run("UPDATE partner_orders SET status=?, payment_status=? WHERE id=?", (os_, ps, oid))
                st.rerun()

    # ---------- Training ----------
    with tabs[3]:
        st.subheader("Training status")
        pts = run("SELECT id, business_name, trained FROM retail_partners WHERE status='onboarded'", fetch=True)
        if not pts:
            st.info("No onboarded partners.")
        for p in pts:
            c1, c2 = st.columns([3, 1])
            c1.write(("✅ " if p["trained"] else "⬜ ") + p["business_name"])
            if c2.button("Toggle trained", key=f"tr{p['id']}"):
                run("UPDATE retail_partners SET trained=? WHERE id=?", (0 if p["trained"] else 1, p["id"]))
                st.rerun()

# ======================================================================
# MODULE: FINANCE
# ======================================================================

"""Payments & accounting, alerts, incentives, dashboard, user management."""

import streamlit as st
import pandas as pd


# ==================== PAYMENTS & ACCOUNTING ====================
def render_payments(user):
    st.header("💰 Payments & Accounting")
    tabs = st.tabs(["Ledger", "Dues & Overdue", "Summary"])

    with tabs[0]:
        led = df("""SELECT p.id, c.name customer, p.ptype, p.amount, p.paid_on,
                           p.mode, p.status, p.due_date
                    FROM payments p JOIN customers c ON c.id=p.customer_id
                    ORDER BY p.id DESC""")
        st.dataframe(led, use_container_width=True, hide_index=True)

        st.subheader("Record a due (expected payment)")
        custs = run("SELECT id, name FROM customers", fetch=True)
        if custs:
            with st.form("due", clear_on_submit=True):
                c1, c2, c3, c4 = st.columns(4)
                cust = c1.selectbox("Customer", custs, format_func=lambda c: c["name"])
                ptype = c2.selectbox("Type", ["advance", "pre_supply", "final"])
                amt = c3.number_input("Amount (₹)", 0.0, step=1000.0)
                dd = c4.date_input("Due date")
                if st.form_submit_button("Add due"):
                    run("""INSERT INTO payments (customer_id, ptype, amount, status, due_date)
                           VALUES (?,?,?,'due',?)""", (cust["id"], ptype, amt, dd.isoformat()))
                    st.success("Due recorded — it will appear in alerts when overdue.")

        dues = run("SELECT id FROM payments WHERE status IN ('due','overdue')", fetch=True)
        if dues:
            c1, c2 = st.columns(2)
            pid = c1.selectbox("Mark payment # as received", [d["id"] for d in dues])
            mode = c2.selectbox("Mode", ["UPI", "Bank transfer", "Cheque", "Cash", "Loan disbursal"])
            if st.button("Mark received"):
                run("UPDATE payments SET status='received', paid_on=?, mode=? WHERE id=?",
                    (today(), mode, pid))
                st.rerun()

    with tabs[1]:
        run("UPDATE payments SET status='overdue' WHERE status='due' AND due_date < ?", (today(),))
        od = df("""SELECT c.name customer, p.ptype, p.amount, p.due_date, p.status
                   FROM payments p JOIN customers c ON c.id=p.customer_id
                   WHERE p.status IN ('due','overdue') ORDER BY p.due_date""")
        if od.empty:
            st.success("No pending dues 🎉")
        else:
            st.dataframe(od, use_container_width=True, hide_index=True)
            st.error(f"Total outstanding: ₹{od['amount'].sum():,.0f}")

    with tabs[2]:
        pays = df("SELECT * FROM payments")
        custs = df("SELECT * FROM customers")
        c1, c2, c3, c4 = st.columns(4)
        received = pays.loc[pays["status"] == "received", "amount"].sum() if not pays.empty else 0
        pending = pays.loc[pays["status"].isin(["due", "overdue"]), "amount"].sum() if not pays.empty else 0
        book = custs["order_value"].sum() if not custs.empty else 0
        c1.metric("Total order book", f"₹{book:,.0f}")
        c2.metric("Collected", f"₹{received:,.0f}")
        c3.metric("Pending dues", f"₹{pending:,.0f}")
        c4.metric("Collection %", f"{(received/book*100):.0f}%" if book else "—")
        if not pays.empty:
            st.subheader("Collections by month")
            rec = pays[pays["status"] == "received"].copy()
            if not rec.empty:
                rec["month"] = pd.to_datetime(rec["paid_on"]).dt.to_period("M").astype(str)
                st.bar_chart(rec.groupby("month")["amount"].sum())
            st.subheader("By payment mode")
            st.bar_chart(pays[pays["status"] == "received"].groupby("mode")["amount"].sum())


# ==================== INCENTIVES ====================
def render_incentives(user):
    st.header("🏆 Sales Incentives")
    inc = df("SELECT * FROM incentives ORDER BY id DESC")
    if inc.empty:
        st.info("No incentives yet — they're created automatically when a lead converts "
                "or a partner order is placed.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Pending", f"₹{inc.loc[inc.status=='pending','amount'].sum():,.0f}")
    c2.metric("Approved", f"₹{inc.loc[inc.status=='approved','amount'].sum():,.0f}")
    c3.metric("Paid", f"₹{inc.loc[inc.status=='paid','amount'].sum():,.0f}")
    st.dataframe(inc, use_container_width=True, hide_index=True)
    st.subheader("Payout by employee")
    st.bar_chart(inc.groupby("employee")["amount"].sum())

    if user["role"] in ("admin", "manager", "accounts"):
        open_i = run("SELECT id FROM incentives WHERE status!='paid'", fetch=True)
        if open_i:
            c1, c2 = st.columns(2)
            iid = c1.selectbox("Incentive #", [i["id"] for i in open_i])
            ns = c2.selectbox("Set status", ["approved", "paid", "pending"])
            if st.button("Update incentive"):
                run("UPDATE incentives SET status=? WHERE id=?", (ns, iid))
                st.rerun()


# ==================== ALERTS ====================
def render_alerts(user):
    st.header("🚨 Alerts")
    run("UPDATE payments SET status='overdue' WHERE status='due' AND due_date < ?", (today(),))

    # Overdue payments
    od = df("""SELECT c.name, p.amount, p.due_date FROM payments p
               JOIN customers c ON c.id=p.customer_id WHERE p.status='overdue'""")
    if not od.empty:
        st.error(f"💸 {len(od)} overdue payment(s), ₹{od['amount'].sum():,.0f} total")
        st.dataframe(od, use_container_width=True, hide_index=True)

    # Low stock
    low = df("""SELECT p.name, p.reorder_level,
                       (SELECT COUNT(*) FROM inventory_units u
                         WHERE u.product_id=p.id AND u.status='in_stock') AS in_stock
                FROM products p""")
    low = low[low["in_stock"] < low["reorder_level"]]
    if not low.empty:
        st.warning(f"📦 {len(low)} product(s) below reorder level")
        st.dataframe(low, use_container_width=True, hide_index=True)

    # Unattended new leads
    stale = df("SELECT name, source, created_at FROM leads WHERE status='new'")
    if not stale.empty:
        st.warning(f"🎯 {len(stale)} new lead(s) not yet contacted")
        st.dataframe(stale, use_container_width=True, hide_index=True)

    # Open tickets
    ot = df("""SELECT c.name, t.ttype, t.description, t.created_at FROM service_tickets t
               JOIN customers c ON c.id=t.customer_id WHERE t.status='open'""")
    if not ot.empty:
        st.info(f"🛠️ {len(ot)} open ticket(s)")
        st.dataframe(ot, use_container_width=True, hide_index=True)

    if od.empty and low.empty and stale.empty and ot.empty:
        st.success("All clear — no alerts right now ✅")


# ==================== DASHBOARD ====================
def render_dashboard(user):
    st.header("📊 Dashboard")
    leads = df("SELECT * FROM leads")
    custs = df("SELECT * FROM customers")
    pays = df("SELECT * FROM payments")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open leads", int((~leads["status"].isin(["won", "lost"])).sum()) if not leads.empty else 0)
    conv = (leads["status"] == "won").mean() * 100 if not leads.empty else 0
    c2.metric("Conversion %", f"{conv:.0f}%")
    c3.metric("Active customers",
              int((custs["stage"] != "closed").sum()) if not custs.empty else 0)
    rec = pays.loc[pays["status"] == "received", "amount"].sum() if not pays.empty else 0
    c4.metric("Collected", f"₹{rec:,.0f}")

    in_stock = run("SELECT COUNT(*) c FROM inventory_units WHERE status='in_stock'", fetch=True)[0]["c"]
    supplied = run("SELECT COUNT(*) c FROM inventory_units WHERE status='supplied'", fetch=True)[0]["c"]
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Units in stock", in_stock)
    c6.metric("Units supplied", supplied)
    green = custs["green_kwh_generated"].sum() if not custs.empty else 0
    c7.metric("Clean energy (kWh)", f"{green:,.0f}")
    c8.metric("CO₂ avoided (t)", f"{green*0.82/1000:,.1f}")

    a, b = st.columns(2)
    with a:
        if not leads.empty:
            st.subheader("Lead funnel")
            order = ["new", "contacted", "site_visit", "quoted", "won", "lost"]
            counts = leads["status"].value_counts().reindex(order).fillna(0)
            st.bar_chart(counts)
    with b:
        if not custs.empty:
            st.subheader("Customers by journey stage")
            st.bar_chart(custs["stage"].value_counts())


# ==================== USER MANAGEMENT (admin) ====================
def render_users(user):
    st.header("👤 User Management")
    users = df("SELECT id, username, full_name, role, active FROM users ORDER BY id")
    users["role"] = users["role"].map(ROLE_LABELS).fillna(users["role"])
    st.dataframe(users, use_container_width=True, hide_index=True)

    with st.form("newuser", clear_on_submit=True):
        st.subheader("Add user")
        c1, c2 = st.columns(2)
        un = c1.text_input("Username")
        fn = c2.text_input("Full name")
        c3, c4 = st.columns(2)
        pw = c3.text_input("Password", type="password")
        role = c4.selectbox("Role", list(ROLE_LABELS.keys()), format_func=lambda r: ROLE_LABELS[r])
        if st.form_submit_button("Create user") and un and pw:
            try:
                run("INSERT INTO users (username, password_hash, full_name, role) VALUES (?,?,?,?)",
                    (un, hash_password(pw), fn, role))
                st.success("User created.")
                st.rerun()
            except Exception:
                st.error("Username already exists.")

    rows = run("SELECT id, username, active FROM users WHERE username != ?", (user["username"],), fetch=True)
    if rows:
        c1, c2 = st.columns(2)
        u = c1.selectbox("User", rows, format_func=lambda r: r["username"])
        if c2.button("Toggle active/deactivate"):
            run("UPDATE users SET active=? WHERE id=?", (0 if u["active"] else 1, u["id"]))
            st.rerun()


# ======================================================================
# MAIN APP
# ======================================================================
st.set_page_config(page_title="SolarOps", page_icon="\u2600\ufe0f", layout="wide")

init_db()

user = require_login()
if not user:
    st.stop()

with st.sidebar:
    st.markdown("### \u2600\ufe0f SolarOps")
    st.write(f"**{user['full_name']}**")
    st.caption(ROLE_LABELS.get(user["role"], user["role"]))
    page = st.radio("Navigate", allowed_pages(user["role"]), label_visibility="collapsed")
    st.divider()
    if st.button("Log out", use_container_width=True):
        logout()
    if st.button("\U0001F504 Refresh data", use_container_width=True):
        st.rerun()
    st.caption("Data syncs with the central database on every action/refresh.")

PAGES = {
    "Dashboard": render_dashboard,
    "Inventory": render_inventory,
    "Leads": render_leads,
    "Customer Journey": render_customers,
    "Retail Partners": render_partners,
    "Payments & Accounting": render_payments,
    "Incentives": render_incentives,
    "Alerts": render_alerts,
    "User Management": render_users,
}

PAGES[page](user)
