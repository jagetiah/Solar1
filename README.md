# ☀️ SolarOps — Solar Dealership Management App

A Python + Streamlit application covering the full operations of a solar dealership:
inventory & demand-supply, leads, the complete customer journey, retail partners,
payments & accounting, alerts, sales incentives, and role-based user access.

Works on **web and mobile** — Streamlit apps run in any browser and the layout is
responsive, so partners can update data from their phones and the owner can track
everything from a laptop.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the printed URL (default http://localhost:8501). On first run the app creates
`solar.db` (SQLite) with demo users and a few products.

### Demo logins

| Role | Username | Password | Sees |
|---|---|---|---|
| Owner / Admin | `admin` | `admin123` | Everything + user management |
| Manager | `manager` | `manager123` | Everything except user management |
| Sales Rep | `ravi` | `sales123` | Dashboard, Leads, Customers, Partners |
| Inventory Staff | `store` | `store123` | Dashboard, Inventory, Alerts |
| Accounts | `accounts` | `acc123` | Dashboard, Payments, Incentives, Alerts |

Change these immediately for real use (Admin → User Management).

## What's inside

- **Inventory & Demand-Supply** — purchase orders, serialized stock (every unit has
  a designated ID), supply-to-customer with automatic inventory update, demand
  forecasts with reorder-gap suggestions, breakage reporting and damage claims.
- **Leads** — source tracking, response-speed measurement (created → first contact),
  rep assignment, activity log (calls, info shared), site visits, quotations,
  conversion % and loss-reason analytics. Marking a lead WON auto-creates the
  customer record and queues the rep's incentive.
- **Customer Journey** — 10-stage pipeline: order confirmation → invoice (with bill
  name/address adjustment) → 30% advance → loan process (docs/bank verification) →
  60–90% pre-supply payment → kit supply → installation → meter testing /
  electricity dept → commissioning → close. Plus warranty & subsidy claims,
  feedback & referrals, green-energy scorecard, service tickets, follow-ups and
  adhoc queries.
- **Retail Partners** — prospect capture (CSV import or manual research), onboarding
  status, partner orders & payments, training tracker, and automatic rep incentives
  on partner business.
- **Payments & Accounting** — full ledger, dues/overdue tracking, collection
  summaries by month and mode.
- **Alerts** — overdue payments, low stock, uncontacted leads, open tickets.
- **Incentives** — auto-created on conversions and partner orders; approve/pay flow.
- **RBAC** — each role only sees its allowed pages (see table above; edit
  `ROLE_PAGES` in `auth.py` to change).

## Two-way data sync

Every screen reads from and writes to the central database on each action, so:
- data entered in the app is in the DB instantly, and
- data written to the DB by any external process appears in the app on the next
  interaction (or the sidebar "Refresh data" button).

## Going to production

- **Database**: SQLite is fine for a single small team. For multiple concurrent
  users across locations, switch to PostgreSQL (e.g., Supabase/Neon) — all queries
  go through `database.py`, so it's a contained change.
- **Hosting**: deploy free on Streamlit Community Cloud, or on any VPS/cloud with
  `streamlit run app.py`. Users just open the URL on phone or desktop; you can
  "Add to Home Screen" on mobile for an app-like experience.
- **Security**: use per-user salts / `bcrypt` for passwords, enable HTTPS, and set
  strong passwords before going live.
- **Prospect scraping**: the Partners module imports prospect CSVs. If you automate
  collection from directories, check each site's terms of service and applicable
  data-protection laws first; exported or licensed lists are often safer.

## Customizing

- Incentive rates: `INCENTIVE_RATE` in `modules/leads.py` (lead conversions) and
  `PARTNER_INCENTIVE_RATE` in `modules/partners.py`.
- Journey stages: `STAGES` in `modules/customers.py`.
- Roles & page access: `ROLE_PAGES` in `auth.py`.
