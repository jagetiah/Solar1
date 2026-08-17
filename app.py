"""
SolarOps — Streamlit app for a solar dealership business.
Run:  streamlit run app.py
Works in any desktop or mobile browser (Streamlit layouts are responsive).
"""

import streamlit as st
from database import init_db
from auth import require_login, allowed_pages, logout, ROLE_LABELS
from modules import inventory, leads, customers, partners, finance

st.set_page_config(page_title="SolarOps", page_icon="☀️", layout="wide")

init_db()

user = require_login()
if not user:
    st.stop()

# ---------- Sidebar navigation ----------
with st.sidebar:
    st.markdown(f"### ☀️ SolarOps")
    st.write(f"**{user['full_name']}**")
    st.caption(ROLE_LABELS.get(user["role"], user["role"]))
    page = st.radio("Navigate", allowed_pages(user["role"]), label_visibility="collapsed")
    st.divider()
    if st.button("Log out", use_container_width=True):
        logout()
    if st.button("🔄 Refresh data", use_container_width=True):
        st.rerun()
    st.caption("Data syncs with the central database on every action/refresh.")

# ---------- Routing (RBAC enforced: only allowed pages appear) ----------
PAGES = {
    "Dashboard": finance.render_dashboard,
    "Inventory": inventory.render,
    "Leads": leads.render,
    "Customer Journey": customers.render,
    "Retail Partners": partners.render,
    "Payments & Accounting": finance.render_payments,
    "Incentives": finance.render_incentives,
    "Alerts": finance.render_alerts,
    "User Management": finance.render_users,
}

PAGES[page](user)
