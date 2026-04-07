"""
GIX Student Purchasing — Streamlit app for student supply orders.

Email (optional): set environment variables before running:
  GIX_SMTP_HOST, GIX_SMTP_PORT (default 587), GIX_SMTP_USER, GIX_SMTP_PASSWORD
  GIX_EMAIL_FROM — sender address
  GIX_NOTIFY_STUDENT, GIX_NOTIFY_INSTRUCTOR, GIX_NOTIFY_COORDINATOR — comma-separated emails

If SMTP is not configured, actions still save; a banner explains that mail was skipped.
"""

# This app is a system where GIX students can submit purchase requests for their course projects.
# It takes the original format used in Excel into a more formal online form with Student and staff portals. 
# When signed in as a student, you specify name and email, add items with quantity and unit price, and submit the request.
# The required inputs from Excel are transferred over into the new system.
# In the staff portal, you can view and edit the orders, and mark them as ordered with an estimated delivery date.
# This app is built to notify student and staff on updates especailly estimated delivery dates.

from __future__ import annotations

import json
import os
import smtplib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

# All orders and the audit log persist in this JSON file next to the script.
DATA_PATH = Path(__file__).resolve().parent / "gix_purchasing_data.json"

# Official GIX program site (University of Washington Global Innovation Exchange).
GIX_WEBSITE_URL = "https://gix.uw.edu"


@dataclass
class LineItem:
    """One line on an order: what to buy, from whom, links, and instructor/coordinator workflow flags."""
    item_id: str
    name: str
    quantity: float
    unit_price: float
    total_price: float
    supplier: str
    project_link: str
    instructor_approval: str = "pending"  # pending | approved | rejected
    ordered: bool = False
    estimated_delivery: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON storage."""
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> LineItem:
        """Rebuild a LineItem from JSON; fills defaults if keys are missing."""
        return LineItem(
            item_id=d.get("item_id") or str(uuid.uuid4()),
            name=d.get("name") or "",
            quantity=float(d.get("quantity") or 0),
            unit_price=float(d.get("unit_price") or 0),
            total_price=float(d.get("total_price") or 0),
            supplier=d.get("supplier") or "",
            project_link=d.get("project_link") or "",
            instructor_approval=d.get("instructor_approval") or "pending",
            ordered=bool(d.get("ordered")),
            estimated_delivery=d.get("estimated_delivery") or "",
        )


@dataclass
class Order:
    """A student’s purchase request: identity, list of LineItems, and metadata (cancelled, notes, timestamps)."""
    order_id: str
    student_name: str
    student_email: str
    items: list[LineItem] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    cancelled: bool = False
    student_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize order + nested items for JSON storage."""
        return {
            "order_id": self.order_id,
            "student_name": self.student_name,
            "student_email": self.student_email,
            "items": [i.to_dict() for i in self.items],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cancelled": self.cancelled,
            "student_note": self.student_note,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Order:
        """Rebuild an Order from JSON."""
        items = [LineItem.from_dict(x) for x in d.get("items") or []]
        return Order(
            order_id=d["order_id"],
            student_name=d.get("student_name") or "",
            student_email=d.get("student_email") or "",
            items=items,
            created_at=d.get("created_at") or "",
            updated_at=d.get("updated_at") or "",
            cancelled=bool(d.get("cancelled")),
            student_note=d.get("student_note") or "",
        )


def now_iso() -> str:
    """UTC timestamp string used for created_at, updated_at, and activity log entries."""
    return datetime.now(timezone.utc).isoformat()


def line_total(quantity: float, unit_price: float) -> float:
    """Line total is always quantity × unit price (rounded to cents)."""
    return round(float(quantity) * float(unit_price), 2)


def load_store() -> dict[str, Any]:
    """Read the JSON file into memory; return empty orders/activity if missing or corrupt."""
    if not DATA_PATH.exists():
        return {"orders": [], "activity": []}
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("orders", [])
        data.setdefault("activity", [])
        return data
    except (json.JSONDecodeError, OSError):
        return {"orders": [], "activity": []}


def save_store(data: dict[str, Any]) -> None:
    """Atomically write the whole store (write temp file, then replace) to avoid half-written JSON on crash."""
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(DATA_PATH)


def log_activity(store: dict[str, Any], actor: str, action: str, detail: str) -> None:
    """Append a human-readable audit entry; trimmed to the last 200 rows for file size."""
    store.setdefault("activity", []).append(
        {"ts": now_iso(), "actor": actor, "action": action, "detail": detail}
    )
    store["activity"] = store["activity"][-200:]


def get_smtp_config() -> dict[str, Any] | None:
    """Return SMTP settings from env, or None if email is not configured (app still works without it)."""
    host = os.environ.get("GIX_SMTP_HOST", "").strip()
    if not host:
        return None
    port = int(os.environ.get("GIX_SMTP_PORT", "587"))
    user = os.environ.get("GIX_SMTP_USER", "").strip()
    password = os.environ.get("GIX_SMTP_PASSWORD", "")
    from_addr = os.environ.get("GIX_EMAIL_FROM", user).strip()
    return {"host": host, "port": port, "user": user, "password": password, "from": from_addr}


def notification_recipients() -> list[str]:
    """Collect unique email addresses from GIX_NOTIFY_STUDENT / INSTRUCTOR / COORDINATOR (comma-separated OK)."""
    out: list[str] = []
    for key in ("GIX_NOTIFY_STUDENT", "GIX_NOTIFY_INSTRUCTOR", "GIX_NOTIFY_COORDINATOR"):
        raw = os.environ.get(key, "")
        for part in raw.split(","):
            e = part.strip()
            if e:
                out.append(e)
    return list(dict.fromkeys(out))


def send_email_all(subject: str, body: str) -> tuple[bool, str]:
    """Send one message to all configured recipients via STARTTLS SMTP; returns (success, user-facing message)."""
    cfg = get_smtp_config()
    recipients = notification_recipients()
    if not cfg:
        return False, "SMTP not configured (set GIX_SMTP_HOST, etc.)."
    if not recipients:
        return False, "No notification emails (set GIX_NOTIFY_* env vars)."
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = cfg["from"]
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
            server.starttls()
            if cfg["user"]:
                server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from"], recipients, msg.as_string())
        return True, f"Sent to {len(recipients)} address(es)."
    except Exception as exc:  # noqa: BLE001
        return False, f"Email failed: {exc}"


def notify_all(store: dict[str, Any], actor: str, action: str, detail: str) -> None:
    """Build email from who did what, send to everyone, log result, and stash status for the Streamlit footer."""
    subject = f"[GIX Purchasing] {action}"
    body = (
        f"Time (UTC): {now_iso()}\n"
        f"Actor: {actor}\n"
        f"Action: {action}\n\n"
        f"{detail}\n"
    )
    ok, msg = send_email_all(subject, body)
    log_activity(store, "system", "email", f"{action} — {msg}")
    st.session_state["_last_email_status"] = msg
    st.session_state["_last_email_ok"] = ok


def orders_list(store: dict[str, Any]) -> list[Order]:
    """Parse the `orders` key in the store into Order objects."""
    return [Order.from_dict(o) for o in store.get("orders", [])]


def save_orders(store: dict[str, Any], orders: list[Order]) -> None:
    """Replace in-memory orders and persist to disk."""
    store["orders"] = [o.to_dict() for o in orders]
    save_store(store)


def find_order(store: dict[str, Any], order_id: str) -> Order | None:
    """Look up a single order by UUID string."""
    for o in orders_list(store):
        if o.order_id == order_id:
            return o
    return None


def init_session() -> None:
    """Initialize Streamlit session keys that must survive reruns (draft line items for the student form)."""
    if "draft_items" not in st.session_state:
        st.session_state.draft_items = []
    if "signin_role" not in st.session_state:
        st.session_state.signin_role = "Student"


def ensure_draft_item_row() -> None:
    """Guarantee at least one empty line item so the new-request form always shows one row."""
    if not st.session_state.draft_items:
        st.session_state.draft_items = [
            {
                "item_id": str(uuid.uuid4()),
                "name": "",
                "quantity": 1.0,
                "unit_price": 0.0,
                "total_price": 0.0,
                "supplier": "",
                "project_link": "",
            }
        ]


def render_student(*, tabbed_in_staff: bool = False) -> None:
    """Student tab: build dynamic line items, submit new orders, and look up orders by email to edit or cancel.

    When ``tabbed_in_staff`` is True, this block sits under the Staff **Student** tab (parent already titled).
    """
    if tabbed_in_staff:
        st.caption("Same form students use on their own — useful for walkthroughs or test data.")

    st.subheader("Create a purchase request")
    st.caption("Add one row per item. Line total is calculated automatically from quantity × unit price.")

    ensure_draft_item_row()

    # --- New request: widgets write into session_state.draft_items dicts keyed by stable widget keys ---
    st.markdown("#### Your details")
    student_name = st.text_input("Student name", key="sn_name")
    student_email = st.text_input("Student email (for your records and notifications)", key="sn_email")

    st.markdown("#### Line items")
    if st.button("+ Add item", key="add_item"):
        st.session_state.draft_items.append(
            {
                "item_id": str(uuid.uuid4()),
                "name": "",
                "quantity": 1.0,
                "unit_price": 0.0,
                "total_price": 0.0,
                "supplier": "",
                "project_link": "",
            }
        )
        st.rerun()

    removed: list[int] = []
    for idx, row in enumerate(st.session_state.draft_items):
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(f"**Item {idx + 1}**")
            with c2:
                if len(st.session_state.draft_items) > 1 and st.button("Remove", key=f"rm_{idx}"):
                    removed.append(idx)

            row["name"] = st.text_input("Item name", value=row["name"], key=f"it_name_{idx}")
            q, u, t = st.columns(3)
            with q:
                row["quantity"] = st.number_input(
                    "Quantity", min_value=0.0, value=float(row["quantity"]), step=1.0, key=f"it_q_{idx}"
                )
            with u:
                row["unit_price"] = st.number_input(
                    "Unit price",
                    min_value=0.0,
                    value=float(row["unit_price"]),
                    step=0.01,
                    key=f"it_u_{idx}",
                )
            with t:
                row["total_price"] = line_total(row["quantity"], row["unit_price"])
                st.metric("Line total", f"${row['total_price']:.2f}")
            row["supplier"] = st.text_input("Supplier", value=row["supplier"], key=f"it_sup_{idx}")
            row["project_link"] = st.text_input(
                "Project / location link", value=row["project_link"], key=f"it_link_{idx}"
            )

    for i in sorted(removed, reverse=True):
        st.session_state.draft_items.pop(i)
    if removed:
        st.rerun()

    if st.button("Submit request", type="primary", key="submit_order"):
        # Only rows with a non-empty item name become LineItems; others are ignored.
        if not student_name.strip() or not student_email.strip():
            st.error("Please enter your name and email. This is required to submit an order.")
            return
        valid_items = [
            LineItem(
                item_id=r["item_id"],
                name=r["name"].strip(),
                quantity=float(r["quantity"]),
                unit_price=float(r["unit_price"]),
                total_price=line_total(r["quantity"], r["unit_price"]),
                supplier=r["supplier"].strip(),
                project_link=r["project_link"].strip(),
            )
            for r in st.session_state.draft_items
            if r["name"].strip()
        ]
        if not valid_items:
            st.error("Add at least one item with a name.")
            return
        store = st.session_state.store
        oid = str(uuid.uuid4())
        order = Order(
            order_id=oid,
            student_name=student_name.strip(),
            student_email=student_email.strip(),
            items=valid_items,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        orders = orders_list(store)
        orders.append(order)
        save_orders(store, orders)
        lines = "\n".join(
            f"  - {it.name} x{it.quantity} @ {it.unit_price} = {it.total_price} ({it.supplier})"
            for it in valid_items
        )
        detail = f"Order {oid}\nStudent: {order.student_name} <{order.student_email}>\n{lines}"
        log_activity(store, "student", "submitted_order", detail)
        notify_all(store, "student", "Student submitted order", detail)
        save_store(store)
        st.session_state.draft_items = []
        ensure_draft_item_row()
        st.success("Request submitted. Instructor and coordinator have been notified (if email is configured).")
        st.rerun()

    # --- Existing orders: match by email (no login); staff fields in the table are read-only here ---
    st.divider()
    st.subheader("Track or change an order")
    st.caption("Look up your orders with the email you used when submitting.")

    lookup = st.text_input("Your email to list orders", key="lookup_email")
    if lookup.strip():
        mine = [o for o in orders_list(st.session_state.store) if o.student_email.lower() == lookup.strip().lower()]
        if not mine:
            st.info("No orders found for that email.")
        else:
            opts = {f"{o.order_id[:8]}… — {o.student_name} ({o.updated_at[:10]})": o.order_id for o in reversed(mine)}
            choice = st.selectbox("Select an order", list(opts.keys()), key="pick_order")
            oid = opts[choice]
            order = find_order(st.session_state.store, oid)
            if order:
                st.markdown("#### Selected order")
                st.write(f"**Status:** {'Cancelled' if order.cancelled else 'Active'}")
                if order.student_note:
                    st.write(f"**Your last note:** {order.student_note}")
                note = st.text_area("Note for staff (optional)", key=f"stu_note_{oid}")

                st.markdown("#### Order lines")
                edf = pd.DataFrame([i.to_dict() for i in order.items])
                edf.insert(0, "student_name", order.student_name)
                edf.insert(1, "student_email", order.student_email)
                # data_editor returns the current table; Save/Cancel use this same-run `edited` dataframe.
                edited = st.data_editor(
                    edf,
                    column_config={
                        "student_name": st.column_config.TextColumn("Student name", disabled=True),
                        "student_email": st.column_config.TextColumn("Student email", disabled=True),
                        "item_id": st.column_config.TextColumn("Item ID", disabled=True),
                        "instructor_approval": st.column_config.TextColumn(
                            "Instructor (staff only)", disabled=True
                        ),
                        "ordered": st.column_config.CheckboxColumn("Ordered (staff only)", disabled=True),
                        "estimated_delivery": st.column_config.TextColumn(
                            "Est. delivery (staff only)", disabled=True
                        ),
                    },
                    hide_index=True,
                    num_rows="dynamic",
                    key=f"editor_{oid}",
                )

                bc1, bc2 = st.columns(2)
                with bc1:
                    if st.button("Save my edits", key=f"save_edits_{oid}"):
                        if order.cancelled:
                            st.warning("This order is cancelled. Contact the coordinator to reopen.")
                        else:
                            new_items: list[LineItem] = []
                            item_df = edited.drop(
                                columns=["student_name", "student_email"], errors="ignore"
                            )
                            for _, r in item_df.iterrows():
                                iid = str(r.get("item_id") or "").strip()
                                if not iid:
                                    iid = str(uuid.uuid4())
                                new_items.append(
                                    LineItem(
                                        item_id=iid,
                                        name=str(r["name"]),
                                        quantity=float(r["quantity"]),
                                        unit_price=float(r["unit_price"]),
                                        total_price=line_total(r["quantity"], r["unit_price"]),
                                        supplier=str(r["supplier"]),
                                        project_link=str(r["project_link"]),
                                        instructor_approval=str(r["instructor_approval"] or "pending"),
                                        ordered=bool(r["ordered"]),
                                        estimated_delivery=str(r["estimated_delivery"] or ""),
                                    )
                                )
                            order.items = new_items
                            order.updated_at = now_iso()
                            order.student_note = note.strip()
                            store = st.session_state.store
                            all_o = orders_list(store)
                            for i, o in enumerate(all_o):
                                if o.order_id == oid:
                                    all_o[i] = order
                                    break
                            save_orders(store, all_o)
                            d = f"Order {oid} updated by student.\n{note}"
                            log_activity(store, "student", "student_revised_order", d)
                            notify_all(store, "student", "Student revised order", d)
                            save_store(store)
                            st.success("Updates saved and notifications sent.")
                            st.rerun()
                with bc2:
                    if st.button("Cancel this order", key=f"cancel_ord_{oid}"):
                        store = st.session_state.store
                        o2 = find_order(store, oid)
                        if o2:
                            o2.cancelled = True
                            o2.updated_at = now_iso()
                            o2.student_note = note.strip()
                            all_o = orders_list(store)
                            for i, o in enumerate(all_o):
                                if o.order_id == oid:
                                    all_o[i] = o2
                                    break
                            save_orders(store, all_o)
                            d = f"Order {oid} cancelled by student."
                            log_activity(store, "student", "student_cancelled_order", d)
                            notify_all(store, "student", "Student cancelled order", d)
                            save_store(store)
                            st.success("Order marked cancelled.")
                            st.rerun()


def render_instructor() -> None:
    """Instructor tab: one expandable block per order; edit approval per line and persist + notify."""
    st.header("Instructor")
    st.subheader("Review purchase requests")
    st.caption("Open an order below. Set each line to Approved or Rejected, then save.")

    store = st.session_state.store
    orders = orders_list(store)
    active = [o for o in orders if not o.cancelled]
    if not active:
        st.info("No active orders.")
        return

    for order in active:
        with st.expander(f"{order.student_name} — {order.order_id[:8]}… ({order.student_email})", expanded=False):
            df = pd.DataFrame([i.to_dict() for i in order.items])
            df.insert(0, "student_name", order.student_name)
            df.insert(1, "student_email", order.student_email)
            edited = st.data_editor(
                df,
                column_config={
                    "student_name": st.column_config.TextColumn("Student name", disabled=True),
                    "student_email": st.column_config.TextColumn("Student email", disabled=True),
                    "item_id": st.column_config.TextColumn("Item ID", disabled=True),
                    "instructor_approval": st.column_config.SelectboxColumn(
                        "Approval",
                        options=["pending", "approved", "rejected"],
                        required=True,
                    ),
                    "ordered": st.column_config.CheckboxColumn("Ordered", disabled=True),
                    "estimated_delivery": st.column_config.TextColumn("Est. delivery", disabled=True),
                },
                hide_index=True,
                key=f"inst_{order.order_id}",
            )
            if st.button("Save instructor decision", key=f"isave_{order.order_id}"):
                new_items: list[LineItem] = []
                item_df = edited.drop(
                    columns=["student_name", "student_email"], errors="ignore"
                )
                for _, r in item_df.iterrows():
                    # Preserve coordinator fields from the previous LineItem when rebuilding from the grid.
                    orig = next((x for x in order.items if x.item_id == str(r["item_id"])), None)
                    new_items.append(
                        LineItem(
                            item_id=str(r["item_id"]),
                            name=str(r["name"]),
                            quantity=float(r["quantity"]),
                            unit_price=float(r["unit_price"]),
                            total_price=line_total(r["quantity"], r["unit_price"]),
                            supplier=str(r["supplier"]),
                            project_link=str(r["project_link"]),
                            instructor_approval=str(r["instructor_approval"]),
                            ordered=orig.ordered if orig else bool(r["ordered"]),
                            estimated_delivery=orig.estimated_delivery if orig else str(r["estimated_delivery"] or ""),
                        )
                    )
                order.items = new_items
                order.updated_at = now_iso()
                all_o = orders_list(store)
                for i, o in enumerate(all_o):
                    if o.order_id == order.order_id:
                        all_o[i] = order
                        break
                save_orders(store, all_o)
                summary = "\n".join(f"  {it.name}: {it.instructor_approval}" for it in new_items)
                d = f"Order {order.order_id}\n{summary}"
                log_activity(store, "instructor", "instructor_review", d)
                notify_all(store, "instructor", "Instructor updated approvals", d)
                save_store(store)
                st.success("Saved. Notifications sent.")
                st.rerun()


def render_coordinator() -> None:
    """Coordinator tab: mark lines as ordered and set estimated delivery; emails everyone on save."""
    st.header("Coordinator")
    st.subheader("Place orders & delivery dates")
    st.caption(
        "Check **Ordered** and enter an estimated delivery date (YYYY-MM-DD) for approved lines. "
        "Saving notifies everyone; the student sees that the order was placed."
    )

    store = st.session_state.store
    orders = orders_list(store)
    active = [o for o in orders if not o.cancelled]
    if not active:
        st.info("No active orders.")
        return

    for order in active:
        with st.expander(f"{order.student_name} — {order.order_id[:8]}…", expanded=False):
            df = pd.DataFrame([i.to_dict() for i in order.items])
            df.insert(0, "student_name", order.student_name)
            df.insert(1, "student_email", order.student_email)
            edited = st.data_editor(
                df,
                column_config={
                    "student_name": st.column_config.TextColumn("Student name", disabled=True),
                    "student_email": st.column_config.TextColumn("Student email", disabled=True),
                    "item_id": st.column_config.TextColumn("Item ID", disabled=True),
                    "name": st.column_config.TextColumn("Item", disabled=True),
                    "quantity": st.column_config.NumberColumn("Qty", disabled=True),
                    "unit_price": st.column_config.NumberColumn("Unit $", disabled=True),
                    "total_price": st.column_config.NumberColumn("Total $", disabled=True),
                    "supplier": st.column_config.TextColumn("Supplier", disabled=True),
                    "project_link": st.column_config.TextColumn("Project link"),
                    "instructor_approval": st.column_config.TextColumn("Instructor", disabled=True),
                    "ordered": st.column_config.CheckboxColumn("Ordered"),
                    "estimated_delivery": st.column_config.TextColumn("Est. delivery (YYYY-MM-DD)"),
                },
                hide_index=True,
                key=f"cord_{order.order_id}",
            )
            if st.button("Save coordinator updates", key=f"csave_{order.order_id}"):
                new_items: list[LineItem] = []
                item_df = edited.drop(
                    columns=["student_name", "student_email"], errors="ignore"
                )
                for _, r in item_df.iterrows():
                    new_items.append(
                        LineItem(
                            item_id=str(r["item_id"]),
                            name=str(r["name"]),
                            quantity=float(r["quantity"]),
                            unit_price=float(r["unit_price"]),
                            total_price=line_total(r["quantity"], r["unit_price"]),
                            supplier=str(r["supplier"]),
                            project_link=str(r["project_link"]),
                            instructor_approval=str(r["instructor_approval"]),
                            ordered=bool(r["ordered"]),
                            estimated_delivery=str(r["estimated_delivery"] or ""),
                        )
                    )
                order.items = new_items
                order.updated_at = now_iso()
                all_o = orders_list(store)
                for i, o in enumerate(all_o):
                    if o.order_id == order.order_id:
                        all_o[i] = order
                        break
                save_orders(store, all_o)
                # Email body highlights approved lines that are marked ordered (student-facing signal).
                lines = []
                for it in new_items:
                    if it.ordered and it.instructor_approval == "approved":
                        lines.append(f"  {it.name}: ordered, est. {it.estimated_delivery or 'TBD'}")
                d = f"Order {order.order_id} for {order.student_email}\n" + (
                    "\n".join(lines) if lines else "(No newly marked ordered lines — table saved as shown.)"
                )
                log_activity(store, "coordinator", "coordinator_updated_fulfillment", d)
                notify_all(store, "coordinator", "Coordinator updated order / delivery", d)
                save_store(store)
                st.success("Saved. Student and staff notified.")
                st.rerun()


def render_about() -> None:
    """About page: project summary and link to the GIX program website."""
    st.header("About")
    st.markdown(
        """
        **GIX Student Purchasing** is a small web app for **Global Innovation Exchange (GIX)** students who need
        to order supplies for course projects.

        - **Students** fill out a structured request: contact info, line items (quantity, unit price, supplier,
          project links), and submit for review.
        - **Instructors** approve or reject each line.
        - **Coordinators** record when items are ordered and add estimated delivery dates.

        Data is saved locally for the class demo. Optional email settings can notify students and staff when
        someone submits or updates an order.

        Learn more about the program at the link below.
        """
    )
    st.link_button("Visit the GIX website", GIX_WEBSITE_URL, use_container_width=False)


def main() -> None:
    """App entry: reload JSON each run; UI depends on sidebar role (student vs staff)."""
    st.set_page_config(page_title="GIX Student Purchasing", page_icon="📦", layout="wide")
    init_session()
    # Fresh load each rerun so multiple browser sessions see the same orders after any save.
    st.session_state.store = load_store()

    with st.sidebar:
        st.header("GIX Purchasing")
        page = st.radio(
            "Page",
            ["Home", "About"],
            key="nav_page",
            help="Switch between the purchasing app and project information.",
        )
        st.divider()
        st.caption("Choose who is using this browser session.")
        role = st.radio(
            "Sign in as",
            ["Student", "Instructor", "Coordinator"],
            key="signin_role",
            help="Students only see the purchase request form. Instructors and coordinators see every section.",
        )
        is_student = role == "Student"
        is_staff = not is_student

    st.title("GIX Student Purchasing")
    if page == "About":
        render_about()
    else:
        if is_student:
            st.header("Student portal")
            st.caption("Submit new supply requests and manage your existing orders in one place.")
        else:
            st.header("Staff workspace")
            st.caption(
                "Use the tabs to move between workflows. **Student** matches the student-only screen; "
                "**Instructor** and **Coordinator** are for approvals and purchasing."
            )

        cfg = get_smtp_config()
        recips = notification_recipients()
        if is_staff:
            st.subheader("Email & notifications")
            if not cfg or not recips:
                st.info(
                    "Configure `GIX_SMTP_*` and `GIX_NOTIFY_STUDENT`, `GIX_NOTIFY_INSTRUCTOR`, "
                    "`GIX_NOTIFY_COORDINATOR` environment variables to send mail. Data is still saved locally."
                )
            else:
                st.caption("Email notifications are enabled.")

        if is_student:
            render_student()
        else:
            st.divider()
            st.subheader("Workflows")
            tab_student, tab_inst, tab_coord = st.tabs(["Student", "Instructor", "Coordinator"])
            with tab_student:
                st.header("Student")
                st.caption("Request form and order lookup — same experience as the student portal.")
                render_student(tabbed_in_staff=True)
            with tab_inst:
                render_instructor()
            with tab_coord:
                render_coordinator()

            st.divider()
            st.header("Activity & audit")
            with st.expander("Open recent activity log", expanded=False):
                # Newest-first slice of persisted log (includes email attempts logged as actor "system").
                acts = list(reversed(st.session_state.store.get("activity", [])[-50:]))
                if not acts:
                    st.write("No activity yet.")
                else:
                    for a in acts:
                        st.text(
                            f"{a.get('ts', '')} | {a.get('actor', '')} | {a.get('action', '')} — {a.get('detail', '')[:120]}…"
                        )

        # Set by notify_all() on the most recent action in this session (success or failure text).
        status = st.session_state.get("_last_email_status")
        if status:
            ok = st.session_state.get("_last_email_ok", False)
            if ok:
                st.success(status)
            else:
                st.warning(status)


if __name__ == "__main__":
    main()
