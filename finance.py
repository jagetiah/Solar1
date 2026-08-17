"""Payments & accounting, alerts, incentives, dashboard, user management."""

import streamlit as st
import pandas as pd
from database import run, df, today, hash_password
from auth import ROLE_LABELS


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
