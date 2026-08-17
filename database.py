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
