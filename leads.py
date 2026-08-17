"""Lead management: capture with source, response speed, rep assignment,
site visits, info sharing, quotations, conversion %, loss reasons, incentives."""

import streamlit as st
import pandas as pd
from datetime import datetime
from database import run, df, today, now

SOURCES = ["Referral", "Website", "Walk-in", "Facebook/Instagram", "Google", "Retailer", "Exhibition", "Other"]
LOST_REASONS = ["Price too high", "Chose competitor", "Financing not approved",
                "Site not feasible", "Postponed decision", "Not reachable", "Other"]
INCENTIVE_RATE = 0.01  # 1% of quote value on conversion — adjust to your policy


def _reps():
    return [r["username"] for r in run(
        "SELECT username FROM users WHERE role IN ('sales','manager') AND active=1", fetch=True)]


def render(user):
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
