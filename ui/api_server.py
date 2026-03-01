from __future__ import annotations

import json
import os
import re
import smtplib
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import boto3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

REGION = "us-east-1"
SES_FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "HackFusion Pharmacy")
SUPERVISOR_STATE = Path(__file__).resolve().parents[1] / "supervisor-agent" / "supervisor_agent_state.json"

agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
dynamodb_client = boto3.client("dynamodb", region_name=REGION)
sesv2 = boto3.client("sesv2", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
checkouts: dict[str, dict[str, Any]] = {}

app = FastAPI(title="Agentic Pharmacy API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InvokeRequest(BaseModel):
    prompt: str


class CheckoutStartRequest(BaseModel):
    prompt: str


class CheckoutConfirmRequest(BaseModel):
    checkout_id: str
    confirm: bool


class CheckoutPayRequest(BaseModel):
    checkout_id: str
    pay: bool


class OrderRequest(BaseModel):
    medicine_name: str
    quantity: int


class InvoiceEmailRequest(BaseModel):
    email: str
    invoice: dict[str, Any]


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


def build_trace_timeline(traces: list[dict]) -> list[dict]:
    timeline = []
    for idx, event in enumerate(traces, start=1):
        if not isinstance(event, dict):
            timeline.append({"step": idx, "stage": "unknown", "summary": str(event)[:220]})
            continue
        keys = list(event.keys())
        stage = keys[0] if keys else "unknown"
        val = event.get(stage)
        if isinstance(val, dict):
            summary = f"{stage}: {', '.join(list(val.keys())[:4])}"
        else:
            summary = f"{stage}: {str(val)[:180]}"
        timeline.append({"step": idx, "stage": stage, "summary": summary})
    return timeline


def get_supervisor_ids() -> tuple[str, str]:
    if not SUPERVISOR_STATE.exists():
        raise HTTPException(status_code=500, detail="supervisor_agent_state.json missing")
    state = json.loads(SUPERVISOR_STATE.read_text(encoding="utf-8"))
    if "agent_id" not in state or "agent_alias_id" not in state:
        raise HTTPException(status_code=500, detail="supervisor state missing agent_id/agent_alias_id")
    return state["agent_id"], state["agent_alias_id"]


def extract_intent_from_text(text: str):
    qty_match = re.search(r"(\d+)\s+([A-Za-z0-9®\-\s,/]+)", text)
    if qty_match:
        return qty_match.group(2).strip(), int(qty_match.group(1))
    return None, 1


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (value or "").lower(), flags=re.UNICODE)).strip()


def parse_quantity(text: str) -> int:
    m = re.search(r"\b(\d+)\b", text or "")
    if not m:
        return 1
    return max(1, int(m.group(1)))


def extract_medicine_candidate(text: str) -> str:
    cleaned = normalize_text(text or "")
    cleaned = re.sub(
        r"\b(order|buy|get|need|want|please|tablets?|capsules?|ml|mg|for me|thats all|that s all)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b\d+\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def resolve_medicine_from_catalog(prompt: str) -> str | None:
    rows = scan_all_items("Medicines")
    normalized_prompt = normalize_text(prompt or "")

    # Exact inclusion first.
    best_name = None
    best_len = -1
    for row in rows:
        name = str(row.get("medicine_name", "")).strip()
        norm = normalize_text(name)
        if norm and norm in normalized_prompt and len(norm) > best_len:
            best_name = name
            best_len = len(norm)
    if best_name:
        return best_name

    # Fuzzy token overlap fallback.
    candidate_text = extract_medicine_candidate(prompt or "")
    candidate_tokens = set(t for t in normalize_text(candidate_text).split(" ") if len(t) > 2)
    if not candidate_tokens:
        return None
    best_score = 0
    best_match = None
    for row in rows:
        name = str(row.get("medicine_name", "")).strip()
        name_tokens = [t for t in normalize_text(name).split(" ") if len(t) > 2]
        if not name_tokens:
            continue
        score = sum(1 for token in name_tokens if token in candidate_tokens)
        if score > best_score:
            best_score = score
            best_match = name
    if best_score >= 1:
        return best_match
    return None


def get_medicine(medicine_name: str | None):
    if not medicine_name:
        return None
    table = dynamodb.Table("Medicines")
    item = table.get_item(Key={"medicine_name": medicine_name}).get("Item")
    return to_native(item) if item else None


def run_policy_check(medicine: dict | None, quantity: int) -> tuple[bool, str]:
    if not medicine:
        return False, "Medicine not found."
    if bool(medicine.get("requires_prescription", False)):
        return False, f"{medicine.get('medicine_name', 'Medicine')} requires prescription."
    stock = int(medicine.get("stock", 0))
    if stock < quantity:
        return False, f"Requested {quantity}, available {stock}."
    return True, "Approved"


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




def scan_all_items(table_name: str):
    table = dynamodb.Table(table_name)
    items = []
    scan_kwargs = {}
    while True:
        resp = table.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return to_native(items)


def place_order_atomic(medicine_name: str, quantity: int):
    order_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        dynamodb_client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": "Medicines",
                        "Key": {"medicine_name": {"S": medicine_name}},
                        "UpdateExpression": "SET stock = stock - :q",
                        "ConditionExpression": "attribute_exists(medicine_name) AND stock >= :q",
                        "ExpressionAttributeValues": {":q": {"N": str(quantity)}},
                        "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
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
        med = dynamodb_client.get_item(
            TableName="Medicines",
            Key={"medicine_name": {"S": medicine_name}},
            ConsistentRead=True,
        ).get("Item", {})
        new_stock = int(med.get("stock", {}).get("N", "0"))
        return {
            "execution_status": "SUCCESS",
            "inventory_updated": True,
            "order_created": True,
            "order_id": order_id,
            "new_stock": new_stock,
            "reason": "ATOMIC_TRANSACTION_COMMITTED",
        }
    except Exception as exc:
        return {
            "execution_status": "FAILED",
            "inventory_updated": False,
            "order_created": False,
            "order_id": None,
            "new_stock": None,
            "reason": str(exc),
        }

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/medicine")
def medicine(medicine_name: str):
    med = get_medicine(medicine_name)
    if not med:
        raise HTTPException(status_code=404, detail="Medicine not found")
    med["found"] = True
    return med


@app.post("/invoke")
def invoke(req: InvokeRequest):
    agent_id, alias_id = get_supervisor_ids()
    session_id = str(uuid.uuid4())

    start = time.time()
    response = agent_runtime.invoke_agent(
        agentId=agent_id,
        agentAliasId=alias_id,
        sessionId=session_id,
        inputText=req.prompt,
        enableTrace=True,
    )

    output = ""
    traces = []
    for event in response["completion"]:
        if "chunk" in event:
            output += event["chunk"]["bytes"].decode("utf-8")
        if "trace" in event:
            traces.append(to_native(event["trace"]))

    latency_ms = int((time.time() - start) * 1000)

    parsed = None
    try:
        parsed = json.loads(output)
    except Exception:
        pass

    medicine_name = parsed.get("medicine_name") if isinstance(parsed, dict) else None
    quantity = int(parsed.get("quantity", 1)) if isinstance(parsed, dict) else 1
    if not medicine_name:
        medicine_name, q_guess = extract_intent_from_text(req.prompt)
        quantity = q_guess if quantity == 1 else quantity

    medicine = get_medicine(medicine_name)
    score = estimate_suggestion_score(medicine, quantity)
    approved = "REJECT" not in output.upper()
    trace_timeline = build_trace_timeline(traces)

    run_doc = {
        "run_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_prompt": req.prompt,
        "supervisor_response": output,
        "trace_count": len(traces),
        "latency_ms": latency_ms,
        "approved": approved,
        "medicine_name": medicine_name or "",
        "quantity": quantity,
        "suggestion_score": score,
        "trace_timeline": trace_timeline,
    }
    dynamodb.Table("AgentRuns").put_item(Item=run_doc)

    return {
        "response": output,
        "approved": approved,
        "latency_ms": latency_ms,
        "trace_count": len(traces),
        "suggestion_score": score,
        "trace_timeline": trace_timeline,
        "medicine": medicine,
    }


@app.get("/orders")
def orders(limit: int = 1000):
    rows = scan_all_items("Orders")
    rows.sort(key=lambda x: x.get("purchase_date", x.get("created_at", "")), reverse=True)
    return {"items": rows[:limit]}


@app.get("/medicines")
def medicines(limit: int = 1000):
    rows = scan_all_items("Medicines")
    rows.sort(key=lambda x: x.get("medicine_name", ""))
    return {"items": rows[:limit]}


@app.post("/order")
def order(req: OrderRequest):
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")
    medicine_row = get_medicine(req.medicine_name)
    approved, reason = run_policy_check(medicine_row, req.quantity)
    if not approved:
        return {
            "execution_status": "FAILED",
            "inventory_updated": False,
            "order_created": False,
            "order_id": None,
            "new_stock": medicine_row.get("stock") if medicine_row else None,
            "reason": reason,
        }
    return place_order_atomic(req.medicine_name, req.quantity)


@app.post("/checkout/start")
def checkout_start(req: CheckoutStartRequest):
    quantity = parse_quantity(req.prompt)
    medicine_name = resolve_medicine_from_catalog(req.prompt)
    medicine_row = get_medicine(medicine_name)
    approved, reason = run_policy_check(medicine_row, quantity)
    suggestion = estimate_suggestion_score(medicine_row, quantity)

    checkout_id = str(uuid.uuid4())
    started = time.time()
    checkouts[checkout_id] = {
        "checkout_id": checkout_id,
        "started_at": started,
        "prompt": req.prompt,
        "quantity": quantity,
        "medicine_name": medicine_name or "",
        "medicine": medicine_row,
        "trace_timeline": [
            {"step": 1, "stage": "IntentExtractionAgent", "summary": f"medicine={medicine_name or 'UNKNOWN'}, qty={quantity}"},
            {
                "step": 2,
                "stage": "SafetyPolicyAgent",
                "summary": (
                    f"stock={medicine_row.get('stock')}, prescription={medicine_row.get('requires_prescription')}"
                    if medicine_row
                    else "Rejected: medicine not found"
                ),
            },
        ],
        "status": "PENDING_CONFIRMATION" if approved else "REJECTED",
        "suggestion_score": suggestion,
    }

    if not medicine_name or not medicine_row:
        return {
            "status": "REJECTED",
            "checkout_id": checkout_id,
            "message": "I could not identify a valid medicine name from your prompt. Please say one medicine clearly.",
            "trace_timeline": checkouts[checkout_id]["trace_timeline"],
            "suggestion_score": suggestion,
        }

    if not approved:
        return {
            "status": "REJECTED",
            "checkout_id": checkout_id,
            "medicine_name": medicine_name,
            "quantity": quantity,
            "message": f"Order rejected: {reason}",
            "trace_timeline": checkouts[checkout_id]["trace_timeline"],
            "suggestion_score": suggestion,
        }

    total = float(medicine_row.get("price", 0)) * quantity
    return {
        "status": "PENDING_CONFIRMATION",
        "checkout_id": checkout_id,
        "medicine_name": medicine_name,
        "quantity": quantity,
        "unit_price": float(medicine_row.get("price", 0)),
        "total_price": total,
        "message": f"Confirm order: {quantity} x {medicine_name}. Total cost: {total:.2f}",
        "trace_timeline": checkouts[checkout_id]["trace_timeline"],
        "suggestion_score": suggestion,
    }


@app.post("/checkout/confirm")
def checkout_confirm(req: CheckoutConfirmRequest):
    state = checkouts.get(req.checkout_id)
    if not state:
        raise HTTPException(status_code=404, detail="checkout_id not found")
    if state["status"] not in {"PENDING_CONFIRMATION", "PENDING_PAYMENT"}:
        raise HTTPException(status_code=409, detail=f"checkout is {state['status']}")

    if not req.confirm:
        state["status"] = "CANCELED"
        state["trace_timeline"].append({"step": len(state["trace_timeline"]) + 1, "stage": "SupervisorAgent", "summary": "Canceled by user at confirmation"})
        return {"status": "CANCELED", "checkout_id": req.checkout_id, "message": "Order canceled by user."}

    state["status"] = "PENDING_PAYMENT"
    state["trace_timeline"].append({"step": len(state["trace_timeline"]) + 1, "stage": "SupervisorAgent", "summary": "User confirmed order; awaiting payment"})
    total = float(state["medicine"].get("price", 0)) * int(state["quantity"])
    return {
        "status": "PENDING_PAYMENT",
        "checkout_id": req.checkout_id,
        "medicine_name": state["medicine_name"],
        "quantity": state["quantity"],
        "total_price": total,
        "message": f"Proceed to payment of {total:.2f}.",
    }


@app.post("/checkout/pay")
def checkout_pay(req: CheckoutPayRequest):
    state = checkouts.get(req.checkout_id)
    if not state:
        raise HTTPException(status_code=404, detail="checkout_id not found")
    if state["status"] != "PENDING_PAYMENT":
        raise HTTPException(status_code=409, detail=f"checkout is {state['status']}")

    if not req.pay:
        state["status"] = "CANCELED"
        state["trace_timeline"].append({"step": len(state["trace_timeline"]) + 1, "stage": "SupervisorAgent", "summary": "Canceled by user at payment"})
        return {"status": "CANCELED", "checkout_id": req.checkout_id, "message": "Payment canceled. Order not placed."}

    state["trace_timeline"].append({"step": len(state["trace_timeline"]) + 1, "stage": "ActionAgent", "summary": "Calling place_order_atomic"})
    resp = place_order_atomic(state["medicine_name"], int(state["quantity"]))
    approved = resp.get("execution_status") == "SUCCESS"
    state["status"] = "PAID" if approved else "FAILED"
    state["trace_timeline"].append(
        {
            "step": len(state["trace_timeline"]) + 1,
            "stage": "SupervisorAgent",
            "summary": "Committed to DynamoDB" if approved else f"Failed: {resp.get('reason')}",
        }
    )

    latency_ms = int((time.time() - float(state["started_at"])) * 1000)
    run_doc = {
        "run_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_prompt": state["prompt"],
        "supervisor_response": "PAID" if approved else "FAILED",
        "trace_count": len(state["trace_timeline"]),
        "latency_ms": latency_ms,
        "approved": approved,
        "medicine_name": state["medicine_name"],
        "quantity": int(state["quantity"]),
        "suggestion_score": int(state["suggestion_score"]),
        "trace_timeline": state["trace_timeline"],
    }
    dynamodb.Table("AgentRuns").put_item(Item=run_doc)

    if not approved:
        return {
            "status": "FAILED",
            "checkout_id": req.checkout_id,
            "message": f"Order failed: {resp.get('reason')}",
            "trace_timeline": state["trace_timeline"],
            "latency_ms": latency_ms,
            "suggestion_score": int(state["suggestion_score"]),
        }

    total_paid = float(state["medicine"].get("price", 0)) * int(state["quantity"])
    invoice = {
        "invoice_id": f"INV-{str(resp.get('order_id', 'NA'))[:8].upper()}",
        "order_id": resp.get("order_id"),
        "medicine_name": state["medicine_name"],
        "quantity": int(state["quantity"]),
        "unit_price": float(state["medicine"].get("price", 0)),
        "total_paid": total_paid,
        "paid_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "status": "PAID",
        "checkout_id": req.checkout_id,
        "message": f"Order placed for {state['quantity']} {state['medicine_name']}. Order ID: {resp.get('order_id')}.",
        "order_id": resp.get("order_id"),
        "new_stock": resp.get("new_stock"),
        "trace_timeline": state["trace_timeline"],
        "latency_ms": latency_ms,
        "suggestion_score": int(state["suggestion_score"]),
        "invoice": invoice,
    }


@app.get("/metrics")
def metrics(limit: int = 1000):
    runs = scan_all_items("AgentRuns")
    runs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    runs = runs[:limit]

    total = len(runs)
    approved = sum(1 for r in runs if r.get("approved"))
    success_rate = round((approved / total) * 100, 2) if total else 0
    avg_latency = round(sum(float(r.get("latency_ms", 0)) for r in runs) / total, 1) if total else 0
    avg_score = round(sum(float(r.get("suggestion_score", 0)) for r in runs) / total, 1) if total else 0

    return {
        "summary": {
            "total_runs": total,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
            "avg_suggestion_score": avg_score,
        },
        "runs": runs,
    }


@app.post("/invoice/email")
def invoice_email(req: InvoiceEmailRequest):
    if not req.email:
        raise HTTPException(status_code=400, detail="email is required")

    inv = req.invoice or {}
    subject = f"Invoice {inv.get('invoice_id', 'N/A')} - Medicine Order Confirmation"
    text_body = (
        f"Invoice ID: {inv.get('invoice_id', 'N/A')}\n"
        f"Order ID: {inv.get('order_id', 'N/A')}\n"
        f"Medicine: {inv.get('medicine_name', 'N/A')}\n"
        f"Quantity: {inv.get('quantity', 'N/A')}\n"
        f"Unit Price: {inv.get('unit_price', 'N/A')}\n"
        f"Total Paid: {inv.get('total_paid', 'N/A')}\n"
        f"Paid At: {inv.get('paid_at', 'N/A')}\n"
    )

    # Prefer SMTP when configured (quick local testing with Gmail App Password).
    if SMTP_USER and SMTP_PASS and SMTP_FROM:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM}>"
            msg["To"] = req.email
            msg.set_content(text_body)

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            return {"sent": True, "provider": "smtp", "email": req.email}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"SMTP send failed: {exc}")

    if not SES_FROM_EMAIL:
        raise HTTPException(
            status_code=500,
            detail="Neither SMTP nor SES is configured on backend",
        )

    try:
        sesv2.send_email(
            FromEmailAddress=SES_FROM_EMAIL,
            Destination={"ToAddresses": [req.email]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject},
                    "Body": {"Text": {"Data": text_body}},
                }
            },
        )
        return {"sent": True, "provider": "ses", "email": req.email}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SES send failed: {exc}")
