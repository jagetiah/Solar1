"""auth.py — login, session handling and role-based access control."""

import streamlit as st
from database import run, hash_password

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
