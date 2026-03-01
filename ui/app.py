from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

REGION = "us-east-1"
SUPERVISOR_STATE = Path(__file__).resolve().parents[1] / "supervisor-agent" / "supervisor_agent_state.json"
DEFAULT_ADMIN_PASSWORD = os.environ.get("UI_ADMIN_PASSWORD", "admin@123")


@st.cache_resource
def clients():
    return {
        "agent_runtime": boto3.client("bedrock-agent-runtime", region_name=REGION),
        "dynamodb_client": boto3.client("dynamodb", region_name=REGION),
        "dynamodb": boto3.resource("dynamodb", region_name=REGION),
    }


def to_native(obj: Any):
    if isinstance(obj, list):
        return [to_native(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_native(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    return obj


def get_supervisor_ids() -> tuple[str, str]:
    if not SUPERVISOR_STATE.exists():
        raise FileNotFoundError("supervisor_agent_state.json not found. Run supervisor-agent create/prepare/deploy first.")

    state = json.loads(SUPERVISOR_STATE.read_text(encoding="utf-8"))
    if "agent_id" not in state or "agent_alias_id" not in state:
        raise RuntimeError("Supervisor state missing agent_id or agent_alias_id. Run deploy_supervisor_agent.py.")
    return state["agent_id"], state["agent_alias_id"]


def extract_intent_from_text(text: str):
    qty_match = re.search(r"(\d+)\s+([A-Za-z0-9®\-\s,/]+)", text)
    if qty_match:
        return qty_match.group(2).strip(), int(qty_match.group(1))
    return None, 1


def estimate_suggestion_score(medicine: dict | None, quantity: int) -> int:
    if not medicine:
        return 25
    stock = int(medicine.get("stock", 0))
    requires_prescription = bool(medicine.get("requires_prescription", False))

    score = 50
    if stock >= quantity:
        score += 30
    if stock >= max(50, quantity * 3):
        score += 10
    if not requires_prescription:
        score += 10
    return max(0, min(100, score))


def parse_json_if_possible(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def build_trace_timeline(traces: list[dict]) -> list[dict]:
    timeline = []
    for idx, event in enumerate(traces, start=1):
        if not isinstance(event, dict):
            timeline.append(
                {
                    "step": idx,
                    "stage": "unknown",
                    "summary": str(event)[:220],
                }
            )
            continue

        keys = list(event.keys())
        stage = keys[0] if keys else "unknown"
        summary = ""
        if stage in event:
            value = event[stage]
            if isinstance(value, dict):
                inner_keys = list(value.keys())
                summary = f"{stage}: {', '.join(inner_keys[:4])}" if inner_keys else stage
            else:
                summary = f"{stage}: {str(value)[:160]}"
        else:
            summary = json.dumps(event)[:220]

        timeline.append(
            {
                "step": idx,
                "stage": stage,
                "summary": summary,
            }
        )
    return timeline


def render_trace_timeline(trace_timeline: list[dict]):
    st.markdown("#### Trace Timeline")
    if not trace_timeline:
        st.info("No trace timeline events captured.")
        return
    tdf = pd.DataFrame(trace_timeline)
    st.dataframe(tdf, use_container_width=True, hide_index=True)


def invoke_supervisor(user_text: str):
    c = clients()["agent_runtime"]
    agent_id, alias_id = get_supervisor_ids()
    session_id = str(uuid.uuid4())

    start = time.time()
    response = c.invoke_agent(
        agentId=agent_id,
        agentAliasId=alias_id,
        sessionId=session_id,
        inputText=user_text,
        enableTrace=True,
    )

    output = ""
    trace_events = []
    for event in response["completion"]:
        if "chunk" in event:
            output += event["chunk"]["bytes"].decode("utf-8")
        if "trace" in event:
            trace_events.append(to_native(event["trace"]))

    latency_ms = int((time.time() - start) * 1000)
    return output, trace_events, latency_ms


def write_agent_run(run: dict):
    table = clients()["dynamodb"].Table("AgentRuns")
    table.put_item(Item=run)


def read_agent_runs(limit: int = 200):
    rows = scan_all_table_rows("AgentRuns")
    rows.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return rows[:limit]




def scan_all_table_rows(table_name: str):
    table = clients()["dynamodb"].Table(table_name)
    rows = []
    scan_kwargs = {}
    while True:
        resp = table.scan(**scan_kwargs)
        rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return [to_native(r) for r in rows]

def get_medicine(medicine_name: str | None):
    if not medicine_name:
        return None
    table = clients()["dynamodb"].Table("Medicines")
    item = table.get_item(Key={"medicine_name": medicine_name}).get("Item")
    return to_native(item) if item else None


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (value or "").lower(), flags=re.UNICODE)).strip()


def resolve_medicine_from_catalog(user_text: str) -> str | None:
    medicines = read_all_medicines()
    normalized_prompt = normalize_text(user_text)

    best_name = None
    best_len = -1
    for med in medicines:
        name = str(med.get("medicine_name", "")).strip()
        norm = normalize_text(name)
        if norm and norm in normalized_prompt and len(norm) > best_len:
            best_name = name
            best_len = len(norm)
    if best_name:
        return best_name

    tokens = set(t for t in normalized_prompt.split(" ") if len(t) > 2)
    best_score = 0
    best_match = None
    for med in medicines:
        name = str(med.get("medicine_name", "")).strip()
        mtokens = [t for t in normalize_text(name).split(" ") if len(t) > 2]
        score = sum(1 for t in mtokens if t in tokens)
        if score > best_score:
            best_score = score
            best_match = name
    if best_score >= 1:
        return best_match
    return None


def place_order_atomic(medicine_name: str, quantity: int) -> dict[str, Any]:
    ddb = clients()["dynamodb_client"]
    order_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        ddb.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": "Medicines",
                        "Key": {"medicine_name": {"S": medicine_name}},
                        "UpdateExpression": "SET stock = stock - :q",
                        "ConditionExpression": "attribute_exists(medicine_name) AND stock >= :q",
                        "ExpressionAttributeValues": {":q": {"N": str(quantity)}},
                    }
                },
                {
                    "Put": {
                        "TableName": "Orders",
                        "Item": {
                            "order_id": {"S": order_id},
                            "medicine_name": {"S": medicine_name},
                            "quantity": {"N": str(quantity)},
                            "status": {"S": "PLACED"},
                            "created_at": {"S": created_at},
                        },
                    }
                },
            ]
        )
        return {
            "execution_status": "SUCCESS",
            "order_id": order_id,
            "reason": "ATOMIC_TRANSACTION_COMMITTED",
        }
    except Exception as exc:
        return {
            "execution_status": "FAILED",
            "order_id": None,
            "reason": str(exc),
        }


def read_recent_orders(limit: int = 200):
    rows = scan_all_table_rows("Orders")
    rows.sort(key=lambda x: x.get("purchase_date", x.get("created_at", "")), reverse=True)
    return rows[:limit]


def read_all_medicines():
    rows = scan_all_table_rows("Medicines")
    rows.sort(key=lambda x: x.get("medicine_name", ""))
    return rows


def _order_count_by_medicine(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        name = r.get("medicine_name")
        if not name:
            continue
        out[name] = out.get(name, 0) + 1
    return out


def comm_graph(approved: bool):
    nodes = ["User", "Supervisor", "Intent", "Safety", "Action", "DynamoDB"]
    edges = [
        ("User", "Supervisor"),
        ("Supervisor", "Intent"),
        ("Intent", "Supervisor"),
        ("Supervisor", "Safety"),
        ("Safety", "Supervisor"),
    ]
    if approved:
        edges += [
            ("Supervisor", "Action"),
            ("Action", "DynamoDB"),
            ("Action", "Supervisor"),
        ]

    pos = {
        "User": (0, 1),
        "Supervisor": (2, 1),
        "Intent": (4, 2),
        "Safety": (4, 1),
        "Action": (4, 0),
        "DynamoDB": (6, 0),
    }

    fig = go.Figure()
    for src, dst in edges:
        x0, y0 = pos[src]
        x1, y1 = pos[dst]
        fig.add_trace(
            go.Scatter(
                x=[x0, x1],
                y=[y0, y1],
                mode="lines",
                line={"width": 3, "color": "#0f172a"},
                hoverinfo="text",
                text=[f"{src} -> {dst}"] * 2,
                showlegend=False,
            )
        )

    fig.add_trace(
        go.Scatter(
            x=[pos[n][0] for n in nodes],
            y=[pos[n][1] for n in nodes],
            mode="markers+text",
            text=nodes,
            textposition="top center",
            textfont={"size": 14, "color": "#0b1220"},
            marker={
                "size": 34,
                "color": "#38bdf8",
                "line": {"color": "#0f172a", "width": 2},
            },
            showlegend=False,
        )
    )

    fig.update_layout(
        title="Agent Communication Trace Map",
        title_font={"size": 20, "color": "#0b1220"},
        xaxis={"visible": False},
        yaxis={"visible": False},
        plot_bgcolor="#f8fafc",
        paper_bgcolor="#f8fafc",
        height=360,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
    )
    return fig


def style_plotly(fig):
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#f8fafc",
        plot_bgcolor="#ffffff",
        font={"color": "#0b1220", "size": 13},
        title_font={"color": "#0b1220", "size": 18},
        legend={"font": {"color": "#0b1220"}},
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor="#e2e8f0",
        linecolor="#94a3b8",
        tickfont={"color": "#0b1220"},
        title_font={"color": "#0b1220"},
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#e2e8f0",
        linecolor="#94a3b8",
        tickfont={"color": "#0b1220"},
        title_font={"color": "#0b1220"},
    )
    return fig


def apply_style():
    st.markdown(
        """
        <style>
        :root {
          --bg-a: #f7fafc;
          --bg-b: #eaf2ff;
          --card: #ffffff;
          --text: #0b1220;
          --subtle: #334155;
          --border: #cbd5e1;
          --accent: #0284c7;
        }
        .stApp {
          background: radial-gradient(1200px 500px at 0% -10%, #dbeafe 0%, transparent 60%),
                      radial-gradient(800px 400px at 100% 0%, #e0f2fe 0%, transparent 50%),
                      linear-gradient(180deg, var(--bg-a) 0%, var(--bg-b) 100%);
          color: var(--text);
        }
        [data-testid="stSidebar"] {
          background: #f8fafc !important;
          border-right: 1px solid #cbd5e1;
        }
        [data-testid="stSidebar"] * {
          color: #0b1220 !important;
        }
        [data-testid="stSidebar"] [role="radiogroup"] label {
          background: #ffffff !important;
          border: 1px solid #cbd5e1 !important;
          border-radius: 10px !important;
          margin-bottom: 8px !important;
          padding: 8px 10px !important;
        }
        h1, h2, h3, h4, h5, h6, p, label, span, div {
          color: var(--text) !important;
        }
        .stMetric {
          background: var(--card);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 10px;
          box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
        }
        .block {
          background: var(--card);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 14px;
          margin-bottom: 10px;
          box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
        }
        [data-testid="stDataFrame"] {
          border: 1px solid var(--border);
          border-radius: 12px;
          overflow: hidden;
        }
        .stButton button {
          background: linear-gradient(90deg, #0284c7 0%, #0369a1 100%);
          color: #ffffff !important;
          border: none;
          border-radius: 12px;
          font-weight: 600;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def require_admin_access() -> bool:
    if "admin_authenticated" not in st.session_state:
        st.session_state["admin_authenticated"] = False

    st.sidebar.markdown("### Admin Access")
    if st.session_state["admin_authenticated"]:
        st.sidebar.success("Authenticated")
        if st.sidebar.button("Logout Admin"):
            st.session_state["admin_authenticated"] = False
        return st.session_state["admin_authenticated"]

    pwd = st.sidebar.text_input("Password", type="password", key="admin_password_input")
    if st.sidebar.button("Login Admin"):
        if pwd == DEFAULT_ADMIN_PASSWORD:
            st.session_state["admin_authenticated"] = True
            st.sidebar.success("Login successful")
        else:
            st.sidebar.error("Invalid password")
    return st.session_state["admin_authenticated"]


def user_chat_page():
    st.subheader("User Chatbot: Multi-Agent Pharmacy Assistant")
    st.write("Ask naturally. Supervisor coordinates Intent -> Safety -> Action with traceability.")

    if "rx_required" not in st.session_state:
        st.session_state["rx_required"] = False
    if "rx_pending_medicine" not in st.session_state:
        st.session_state["rx_pending_medicine"] = ""
    if "rx_pending_quantity" not in st.session_state:
        st.session_state["rx_pending_quantity"] = 0
    if "rx_pending_prompt" not in st.session_state:
        st.session_state["rx_pending_prompt"] = ""
    if "rx_verified_medicines" not in st.session_state:
        st.session_state["rx_verified_medicines"] = set()
    if "rx_uploaded_name" not in st.session_state:
        st.session_state["rx_uploaded_name"] = ""

    user_text = st.text_input("User prompt", value="I want to order 2 Crocin tablets")
    run_btn = st.button("Run Agent Chain", type="primary")

    def render_prescription_controls(widget_suffix: str = ""):
        st.warning(
            f"Prescription required for {st.session_state['rx_pending_medicine']}. "
            "Upload prescription to continue."
        )
        uploaded_rx = st.file_uploader(
            "Upload Prescription (PDF/Image)",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            key=f"rx_upload_widget{widget_suffix}",
        )
        a1, a2 = st.columns(2)
        if uploaded_rx is not None and a1.button("Verify Uploaded Prescription", key=f"verify_rx_btn{widget_suffix}"):
            st.session_state["rx_uploaded_name"] = uploaded_rx.name
            st.session_state["rx_verified_medicines"].add(st.session_state["rx_pending_medicine"])
            st.session_state["rx_required"] = False
            st.success(
                f"Prescription uploaded and verified for "
                f"{st.session_state['rx_pending_medicine']}: {uploaded_rx.name}"
            )
        if a2.button("Cancel Pending Order", key=f"cancel_rx_btn{widget_suffix}"):
            st.session_state["rx_required"] = False
            st.session_state["rx_pending_medicine"] = ""
            st.session_state["rx_pending_quantity"] = 0
            st.session_state["rx_pending_prompt"] = ""
            st.info("Pending order canceled.")

    if st.session_state["rx_required"]:
        render_prescription_controls("_top")

    continue_pending_btn = False
    if (
        st.session_state["rx_pending_medicine"]
        and st.session_state["rx_pending_medicine"] in st.session_state["rx_verified_medicines"]
    ):
        continue_pending_btn = st.button("Continue Pending Order", type="secondary")

    if run_btn or continue_pending_btn:
        active_prompt = (
            st.session_state["rx_pending_prompt"]
            if continue_pending_btn and st.session_state["rx_pending_prompt"]
            else user_text
        )
        # Snapshot database before run for strict post-run verification.
        before_orders = scan_all_table_rows("Orders")
        before_medicines = read_all_medicines()
        before_order_counts = _order_count_by_medicine(before_orders)
        before_stock_map = {
            m.get("medicine_name"): int(m.get("stock", 0))
            for m in before_medicines
            if m.get("medicine_name") is not None
        }

        with st.spinner("Running intent -> safety -> action with traces..."):
            start_ts = time.time()
            _, quantity = extract_intent_from_text(active_prompt)
            medicine_name = resolve_medicine_from_catalog(active_prompt)
            if not quantity:
                quantity = 1
            quantity = int(quantity or 1)
            if continue_pending_btn and st.session_state["rx_pending_quantity"] > 0:
                quantity = int(st.session_state["rx_pending_quantity"])

            trace_timeline: list[dict] = []
            trace_timeline.append(
                {
                    "step": 1,
                    "stage": "IntentExtractionAgent",
                    "summary": f"medicine={medicine_name or 'UNKNOWN'}, qty={quantity}",
                }
            )

            medicine = get_medicine(medicine_name)
            if not medicine:
                approved = False
                output_obj = {
                    "status": "REJECTED",
                    "reason": "Medicine not found",
                    "medicine_name": medicine_name or "",
                    "quantity": quantity,
                }
                trace_timeline.append(
                    {"step": 2, "stage": "SafetyPolicyAgent", "summary": "Rejected: medicine not found"}
                )
            elif bool(medicine.get("requires_prescription", False)):
                if medicine_name not in st.session_state["rx_verified_medicines"]:
                    approved = False
                    st.session_state["rx_required"] = True
                    st.session_state["rx_pending_medicine"] = medicine_name
                    st.session_state["rx_pending_quantity"] = quantity
                    st.session_state["rx_pending_prompt"] = active_prompt
                    output_obj = {
                        "status": "PENDING_PRESCRIPTION",
                        "reason": f"{medicine_name} requires prescription. Upload prescription to continue.",
                        "medicine_name": medicine_name,
                        "quantity": quantity,
                    }
                    trace_timeline.append(
                        {"step": 2, "stage": "SafetyPolicyAgent", "summary": "Pending: prescription required"}
                    )
                else:
                    trace_timeline.append(
                        {"step": 2, "stage": "SafetyPolicyAgent", "summary": "Prescription verified"}
                    )
                    trace_timeline.append(
                        {"step": 3, "stage": "ActionAgent", "summary": "Calling place_order_atomic"}
                    )
                    action_resp = place_order_atomic(medicine_name, quantity)
                    approved = action_resp.get("execution_status") == "SUCCESS"
                    output_obj = {
                        "status": "APPROVED" if approved else "FAILED",
                        "reason": action_resp.get("reason", ""),
                        "order_id": action_resp.get("order_id"),
                        "medicine_name": medicine_name,
                        "quantity": quantity,
                    }
                    trace_timeline.append(
                        {
                            "step": 4,
                            "stage": "SupervisorAgent",
                            "summary": "Committed to DynamoDB" if approved else f"Action failed: {action_resp.get('reason', '')}",
                        }
                    )
                    if approved:
                        st.session_state["rx_pending_medicine"] = ""
                        st.session_state["rx_pending_quantity"] = 0
                        st.session_state["rx_pending_prompt"] = ""
            elif int(medicine.get("stock", 0)) < quantity:
                approved = False
                output_obj = {
                    "status": "REJECTED",
                    "reason": f"Insufficient stock: requested={quantity}, available={int(medicine.get('stock', 0))}",
                    "medicine_name": medicine_name,
                    "quantity": quantity,
                }
                trace_timeline.append(
                    {"step": 2, "stage": "SafetyPolicyAgent", "summary": "Rejected: insufficient stock"}
                )
            else:
                trace_timeline.append(
                    {
                        "step": 2,
                        "stage": "SafetyPolicyAgent",
                        "summary": f"Approved: stock={int(medicine.get('stock', 0))}, prescription=False",
                    }
                )
                trace_timeline.append(
                    {"step": 3, "stage": "ActionAgent", "summary": "Calling place_order_atomic"}
                )
                action_resp = place_order_atomic(medicine_name, quantity)
                approved = action_resp.get("execution_status") == "SUCCESS"
                output_obj = {
                    "status": "APPROVED" if approved else "FAILED",
                    "reason": action_resp.get("reason", ""),
                    "order_id": action_resp.get("order_id"),
                    "medicine_name": medicine_name,
                    "quantity": quantity,
                }
                trace_timeline.append(
                    {
                        "step": 4,
                        "stage": "SupervisorAgent",
                        "summary": "Committed to DynamoDB" if approved else f"Action failed: {action_resp.get('reason', '')}",
                    }
                )

            output = json.dumps(output_obj, ensure_ascii=False)
            traces = trace_timeline
            latency_ms = int((time.time() - start_ts) * 1000)

        score = estimate_suggestion_score(medicine, quantity)

        after_orders = scan_all_table_rows("Orders")
        after_order_counts = _order_count_by_medicine(after_orders)
        after_medicine = get_medicine(medicine_name)
        after_stock = int(after_medicine.get("stock", 0)) if after_medicine else None
        before_stock = before_stock_map.get(medicine_name) if medicine_name else None
        before_order_count = before_order_counts.get(medicine_name, 0) if medicine_name else 0
        after_order_count = after_order_counts.get(medicine_name, 0) if medicine_name else 0

        pending_prescription = output_obj.get("status") == "PENDING_PRESCRIPTION"
        order_added = after_order_count > before_order_count
        stock_reduced = (
            before_stock is not None
            and after_stock is not None
            and after_stock <= (before_stock - quantity)
        )
        db_update_ok = ((not approved) and (not pending_prescription)) or (order_added and stock_reduced)
        decision_label = "PENDING_RX" if pending_prescription else ("APPROVED" if approved else "REJECTED")
        db_update_label = "WAITING_RX" if pending_prescription else ("PASS" if db_update_ok else "FAIL")

        run_id = str(uuid.uuid4())
        run_doc = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_prompt": active_prompt,
            "supervisor_response": output,
            "trace_count": len(trace_timeline),
            "latency_ms": latency_ms,
            "approved": approved,
            "medicine_name": medicine_name or "",
            "quantity": quantity,
            "status": output_obj.get("status", ""),
            "suggestion_score": score,
            "trace_timeline": trace_timeline,
            "db_order_added": bool(order_added),
            "db_stock_reduced": bool(stock_reduced),
            "db_update_ok": bool(db_update_ok),
        }
        write_agent_run(run_doc)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Latency", f"{latency_ms} ms")
        c2.metric("Trace Events", len(trace_timeline))
        c3.metric("Suggestion Score", f"{score}/100")
        c4.metric("Decision", decision_label)
        c5.metric("DB Update", db_update_label)

        st.markdown('<div class="block">', unsafe_allow_html=True)
        st.markdown("#### Agent Response")
        st.code(output, language="json")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("#### Explainability Chain")
        st.write("1. Intent Agent extracted medicine and quantity from user text.")
        st.write("2. Safety Agent validated stock and prescription policy.")
        if approved:
            st.write("3. Action Agent executed order creation and inventory update.")
        else:
            st.write("3. Action Agent was not invoked due to policy rejection.")

        st.markdown("#### Database Verification")
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Order Rows Before", before_order_count)
        v2.metric("Order Rows After", after_order_count)
        v3.metric("Stock Before", before_stock if before_stock is not None else "N/A")
        v4.metric("Stock After", after_stock if after_stock is not None else "N/A")

        if approved and not db_update_ok:
            st.error(
                "Approved response but database update is incomplete. "
                "Order row and stock change are not both reflected in DynamoDB."
            )
            st.warning(
                "ActionAgent likely placed order without running inventory update. "
                "Check ActionAgent tool-call behavior and traces."
            )
        elif approved and db_update_ok:
            st.success("Approved response verified: DynamoDB order + stock updates are consistent.")
        elif output_obj.get("status") == "PENDING_PRESCRIPTION":
            st.warning("Order is waiting for prescription upload. Upload prescription and click Continue Pending Order.")
            render_prescription_controls("_inline")
        else:
            st.info("Rejected request verified: no execution update expected.")

        st.plotly_chart(comm_graph(approved), use_container_width=True)

        with st.expander("Trace Events (Raw)"):
            st.json(trace_timeline)
        render_trace_timeline(trace_timeline)

        if medicine:
            st.markdown("#### Medicine Snapshot")
            st.json(medicine)


def admin_dashboard_page():
    st.subheader("Admin Observability Dashboard")
    runs = read_agent_runs(300)
    orders = read_recent_orders(300)

    if not runs:
        st.info("No AgentRuns yet. Run a user chat first.")
        return

    df = pd.DataFrame(runs)
    df["approved"] = df["approved"].astype(bool)
    df["latency_ms"] = pd.to_numeric(df["latency_ms"], errors="coerce").fillna(0)
    df["suggestion_score"] = pd.to_numeric(df["suggestion_score"], errors="coerce").fillna(0)

    success_rate = round(df["approved"].mean() * 100, 2)
    avg_latency = round(df["latency_ms"].mean(), 1)
    avg_score = round(df["suggestion_score"].mean(), 1)

    c1, c2, c3 = st.columns(3)
    c1.metric("Success Rate", f"{success_rate}%")
    c2.metric("Avg Latency", f"{avg_latency} ms")
    c3.metric("Avg Suggestion Score", f"{avg_score}/100")

    col1, col2 = st.columns(2)
    with col1:
        fig = px.histogram(df, x="latency_ms", nbins=20, title="Latency Distribution")
        fig = style_plotly(fig)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig2 = px.pie(df, names="approved", title="Approval Split", hole=0.45)
        fig2 = style_plotly(fig2)
        fig2.update_traces(textfont={"color": "#0b1220", "size": 13})
        st.plotly_chart(fig2, use_container_width=True)

    if orders:
        odf = pd.DataFrame(orders)
        if "medicine_name" in odf.columns:
            top = odf["medicine_name"].value_counts().head(10).reset_index()
            top.columns = ["medicine_name", "orders"]
            fig3 = px.bar(top, x="medicine_name", y="orders", title="Top Ordered Medicines")
            fig3 = style_plotly(fig3)
            fig3.update_traces(marker={"color": "#0284c7", "line": {"color": "#0f172a", "width": 1}})
            st.plotly_chart(fig3, use_container_width=True)

    st.markdown("#### Recent Agent Runs")
    st.dataframe(df[[
        "timestamp",
        "user_prompt",
        "medicine_name",
        "quantity",
        "approved",
        "suggestion_score",
        "latency_ms",
        "trace_count",
    ]].head(50), use_container_width=True)

    latest = runs[0]
    trace_timeline = latest.get("trace_timeline", [])
    if trace_timeline:
        st.markdown("#### Latest Run Trace Timeline")
        render_trace_timeline(trace_timeline)


def orders_ledger_page():
    st.subheader("Orders Ledger")
    if st.button("Refresh Orders From DynamoDB", key="refresh_orders"):
        st.rerun()
    orders = read_recent_orders(5000)
    if not orders:
        st.info("No orders found.")
        return

    df = pd.DataFrame(orders)
    if "quantity" in df.columns:
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0).astype(int)
    else:
        df["quantity"] = 0

    status_col = df["status"] if "status" in df.columns else pd.Series(["UNKNOWN"] * len(df))
    historical_count = int((status_col == "HISTORICAL").sum())
    ai_count = int((status_col != "HISTORICAL").sum())
    total_count = int(len(df))
    total_qty = int(df["quantity"].sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Orders", total_count)
    c2.metric("Historical Orders", historical_count)
    c3.metric("AI/Live Orders", ai_count)
    c4.metric("Total Units Sold", total_qty)

    st.markdown("#### Order Status Split")
    status_df = status_col.value_counts().reset_index()
    status_df.columns = ["status", "count"]
    fig = px.bar(status_df, x="status", y="count", title="Orders by Status")
    fig = style_plotly(fig)
    fig.update_traces(marker={"color": "#0ea5e9", "line": {"color": "#0f172a", "width": 1}})
    st.plotly_chart(fig, use_container_width=True)

    cols = [
        c for c in [
            "order_id",
            "status",
            "medicine_name",
            "quantity",
            "source",
            "purchase_date",
            "created_at",
            "patient_id",
            "prescription_required",
            "total_price",
        ] if c in df.columns
    ]
    st.markdown("#### Full Order Details")
    all_cols = sorted(df.columns.tolist())
    st.dataframe(df[all_cols], use_container_width=True)


def medicine_inventory_page():
    st.subheader("Medicine Inventory")
    if st.button("Refresh Inventory From DynamoDB", key="refresh_inventory"):
        st.rerun()
    medicines = read_all_medicines()
    if not medicines:
        st.info("No medicines found.")
        return

    df = pd.DataFrame(medicines)
    if "stock" in df.columns:
        df["stock"] = pd.to_numeric(df["stock"], errors="coerce").fillna(0).astype(int)
    else:
        df["stock"] = 0

    total_medicines = len(df)
    total_stock = int(df["stock"].sum())
    low_stock_count = int((df["stock"] <= 20).sum())
    out_of_stock_count = int((df["stock"] <= 0).sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Medicines", total_medicines)
    c2.metric("Total Remaining Stock", total_stock)
    c3.metric("Low Stock (<=20)", low_stock_count)
    c4.metric("Out Of Stock", out_of_stock_count)

    st.markdown("#### Stock by Medicine (Top 20)")
    top_stock = df.sort_values("stock", ascending=False).head(20)
    fig = px.bar(top_stock, x="medicine_name", y="stock", title="Remaining Stock")
    fig = style_plotly(fig)
    fig.update_traces(marker={"color": "#22c55e", "line": {"color": "#0f172a", "width": 1}})
    st.plotly_chart(fig, use_container_width=True)

    if "requires_prescription" in df.columns:
        st.markdown("#### Prescription Requirement Split")
        pres_df = df["requires_prescription"].value_counts().reset_index()
        pres_df.columns = ["requires_prescription", "count"]
        fig2 = px.pie(pres_df, names="requires_prescription", values="count", hole=0.45)
        fig2 = style_plotly(fig2)
        fig2.update_traces(textfont={"color": "#0b1220"})
        st.plotly_chart(fig2, use_container_width=True)

    cols = [
        c for c in [
            "medicine_name",
            "stock",
            "price",
            "requires_prescription",
            "package_size",
            "pzn",
            "product_id",
            "source",
        ] if c in df.columns
    ]
    st.markdown("#### Full Medicine Details")
    all_cols = sorted(df.columns.tolist())
    st.dataframe(df[all_cols], use_container_width=True)


def main():
    st.set_page_config(page_title="Agentic Pharmacy Control Tower", page_icon="A", layout="wide")
    apply_style()

    st.title("Agentic Pharmacy: Traceability + Explainability UI")
    st.caption("User interaction and admin observability across Intent, Safety, Action, and Supervisor agents.")

    mode = st.sidebar.radio(
        "View",
        ["User Chat", "Admin Dashboard", "Orders Ledger", "Medicine Inventory", "System Status"],
        index=0,
    )

    if mode == "User Chat":
        user_chat_page()
    else:
        if not require_admin_access():
            st.warning("Admin access required for this view.")
            st.info("Set a custom password with env var UI_ADMIN_PASSWORD.")
            return

    if mode == "Admin Dashboard":
        admin_dashboard_page()
    elif mode == "Orders Ledger":
        orders_ledger_page()
    elif mode == "Medicine Inventory":
        medicine_inventory_page()
    elif mode == "System Status":
        st.subheader("System Status")
        checks = {
            "Intent state": Path(__file__).resolve().parents[1] / "intent-agent" / "intent_agent_state.json",
            "Safety state": Path(__file__).resolve().parents[1] / "safety-agent" / "safety_agent_state.json",
            "Action state": Path(__file__).resolve().parents[1] / "action-agent" / "action_agent_state.json",
            "Supervisor state": SUPERVISOR_STATE,
        }
        for label, path in checks.items():
            st.write(f"- {label}: {'OK' if path.exists() else 'MISSING'} ({path})")

        st.write("Run order:")
        st.code(
            "python infra/setup_dynamodb.py\n"
            "python infra/import_hackfusion_dataset.py\n"
            "# Note: importer writes Medicines only; it does not write Orders\n"
            "python infra/setup_observability_table.py\n"
            "python infra/deploy_lambdas.py\n"
            "python safety-agent/create_safety_agent.py ...\n"
            "python action-agent/create_action_agent.py ...\n"
            "python supervisor-agent/create_supervisor_agent.py ...\n"
            "streamlit run ui/app.py",
            language="bash",
        )


if __name__ == "__main__":
    main()
