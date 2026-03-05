# Agentic Pharmacy Backend (HackFusion)

Backend and operations repository for a multi-agent pharmacy ordering system built on AWS Bedrock Agents, Lambda tools, DynamoDB, API Gateway, and Streamlit observability UI.

This repository is intentionally backend-first. The React frontend is maintained separately and excluded from this repo.

## Project Links: 
**React App URL**: https://hackfusion.skillrouteai.com
**Streamlite App URL**: https://hackfusion.streamlite.app


## 1. Problem Solved

Build a traceable, explainable AI workflow for medicine ordering with:
- strict safety policy checks (stock, prescription),
- deterministic database updates,
- clear multi-agent orchestration visibility,
- admin observability and auditability.

## 2. High-Level Architecture

User Request -> Supervisor Orchestration -> Intent Extraction -> Safety Validation -> Action Execution -> DynamoDB

Core design principle:
- Lambdas provide capabilities.
- Agents provide decisions.
- Supervisor coordinates flow.

## 3. Agent Design

### IntentExtractionAgent
- Input: natural language prompt.
- Output: structured intent (`medicine_name`, `quantity`).
- No tool/database access.

### SafetyPolicyAgent
- Validates:
  - medicine exists,
  - prescription requirement,
  - stock availability.
- No write operations.

### ActionAgent
- Executes approved actions only.
- Uses atomic order placement path.

### SupervisorAgent
- Coordinates end-to-end sequence.
- Produces explainable trace output.

## 4. Data Layer (DynamoDB)

### Medicines table
- Partition key: `medicine_name`
- Typical attributes:
  - `stock` (N)
  - `price` (N)
  - `requires_prescription` (BOOL)
  - metadata (`package_size`, `pzn`, `product_id`, `source`)

### Orders table
- Partition key: `order_id`
- Typical attributes:
  - `medicine_name`
  - `quantity`
  - `status`
  - `created_at`

### AgentRuns table
- Run-level observability records:
  - prompt, decision, latency, trace_count,
  - suggestion_score,
  - database consistency flags.

## 5. Backend Components

### Lambda tools (`lambdas/`)
- `get_medicine_details`
- `create_order`
- `update_inventory`
- `place_order_atomic` (recommended commit path)
- `list_medicines`
- `list_orders`

### Infra automation (`infra/`)
- DynamoDB setup and seeding
- dataset import
- Lambda deploy/update
- API Gateway setup
- Bedrock invoke permissions
- data-layer verification scripts

### APIs / Services
- API Gateway for frontend/API access
- FastAPI server (`ui/api_server.py`) for local integration and orchestration helpers
- Streamlit app (`ui/app.py`) for user flow + admin observability

## 6. Order Flow (Implemented)

1. Parse and resolve medicine intent from user text.
2. Run policy checks:
   - not found -> reject,
   - prescription required -> pending prescription,
   - low stock -> reject.
3. If approved, execute atomic DB write:
   - decrement inventory,
   - insert order row.
4. Verify DB consistency:
   - order row changed,
   - stock reduced correctly.
5. Persist run telemetry to `AgentRuns`.

## 7. Prescription Gating Flow (Streamlit)

When prescription is required:
- Decision shown as `PENDING_RX`.
- DB update status shown as `WAITING_RX`.
- UI exposes prescription controls.
- After verification, continue pending order without retyping.

## 8. Explainability and Traceability

For every run:
- latency and trace event count,
- decision and DB status,
- raw trace timeline,
- communication trace map,
- explainability chain.

This supports judge/demo clarity and incident debugging.

## 9. Repository Structure

```
action-agent/
intent-agent/
safety-agent/
supervisor-agent/
infra/
lambdas/
ui/
docs/
```

Note: `react-ui/` is excluded in this backend repository.

## 10. Configuration

Region:
- `us-east-1` only.

Common environment variables:
- `AWS_REGION=us-east-1`
- `AWS_DEFAULT_REGION=us-east-1`
- `BEDROCK_AGENT_ROLE_ARN=...`
- `LAMBDA_EXEC_ROLE_ARN=...`
- `UI_ADMIN_PASSWORD=...`

Optional (if using local email API path):
- SMTP/SES variables in local environment only.

## 11. Local Runbook

### Install
```bash
pip install -r ui/requirements.txt
```

### Run Streamlit
```bash
streamlit run ui/app.py
```

### Run FastAPI (optional local API)
```bash
uvicorn ui.api_server:app --reload --port 8000
```

## 12. Streamlit Cloud Deployment (Step-by-Step)

1. Push this backend repo to GitHub (without `react-ui`).
2. In Streamlit Cloud, create app from this repo.
3. Main file path: `ui/app.py`
4. Add secrets in Streamlit Cloud:

```toml
AWS_ACCESS_KEY_ID="..."
AWS_SECRET_ACCESS_KEY="..."
AWS_DEFAULT_REGION="us-east-1"
AWS_REGION="us-east-1"
UI_ADMIN_PASSWORD="admin@123"
```

Optional if needed:
```toml
AWS_SESSION_TOKEN="..."
```

5. Deploy and verify:
   - User Chat run,
   - Admin Dashboard,
   - Orders Ledger,
   - Medicine Inventory.

## 13. Git Notes

This repo includes `.gitignore` to avoid pushing:
- `react-ui/`
- Python cache/runtime artifacts
- local secret/runtime files.

If `react-ui` was previously staged, remove from index once:
```bash
git rm -r --cached react-ui
```

## 14. Demo Checklist

1. Non-prescription successful order -> DB PASS.
2. Prescription medicine -> `PENDING_RX` -> verify -> continue -> DB PASS.
3. Out-of-stock rejection.
4. Admin dashboard shows updated runs/orders/inventory.
