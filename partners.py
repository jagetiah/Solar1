"""Retail partners: prospect discovery (CSV import / manual research capture),
onboarding, inventory orders, payments, training, and rep incentives."""

import streamlit as st
import pandas as pd
from database import run, df, today

PARTNER_INCENTIVE_RATE = 0.005  # 0.5% of partner order value to the driving rep


def _reps():
    return [r["username"] for r in run(
        "SELECT username FROM users WHERE role IN ('sales','manager') AND active=1", fetch=True)]


def render(user):
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
                rep = c5.selectbox("Assigned rep", _reps() or ["(none)"])
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
