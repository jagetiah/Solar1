"""Customer journey: order confirmation → invoice → payments → loan →
supply → installation → meter/commissioning → warranty/subsidy →
feedback/referrals → service & green scorecard → follow-ups & adhoc queries."""

import streamlit as st
import pandas as pd
from database import run, df, today, now

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


def render(user):
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
