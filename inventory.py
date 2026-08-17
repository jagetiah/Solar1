"""Inventory management: purchase orders, serialized stock, kit supply,
demand forecasting with gap suggestions, and breakage/damage claims."""

import streamlit as st
import pandas as pd
from database import run, df, today, now


def render(user):
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
