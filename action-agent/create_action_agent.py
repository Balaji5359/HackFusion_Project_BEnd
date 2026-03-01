import json
import os
import time

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
AGENT_NAME = "ActionAgent"
FOUNDATION_MODEL = "amazon.nova-lite-v1:0"
AGENT_ROLE_ARN = os.environ["BEDROCK_AGENT_ROLE_ARN"]
PLACE_ORDER_ATOMIC_LAMBDA_ARN = os.environ["PLACE_ORDER_ATOMIC_LAMBDA_ARN"]
STATE_FILE = os.path.join(os.path.dirname(__file__), "action_agent_state.json")

client = boto3.client("bedrock-agent", region_name=REGION)

INSTRUCTION = """
You are ActionAgent.
You execute approved actions only.
Rules:
1. Never perform policy validation.
2. Assume caller already approved execution.
3. Use ONLY place_order_atomic(medicine_name, quantity) for execution.
4. Never call create_order or update_inventory directly.
5. If place_order_atomic reports FAILED, return failure and do not claim success.
6. If required fields are missing, ask for exact missing field.
7. Return JSON only with execution evidence.

Required output schema:
{
  "execution_status": "SUCCESS" | "FAILED",
  "inventory_updated": true | false,
  "order_created": true | false,
  "order_id": "string or null",
  "new_stock": "number or null",
  "reason": "string"
}
""".strip()

ACTION_GROUPS = [
    {
        "name": "PlaceOrderAtomicActionGroup",
        "lambda_arn": PLACE_ORDER_ATOMIC_LAMBDA_ARN,
        "function_schema": {
            "functions": [
                {
                    "name": "place_order_atomic",
                    "description": "Atomically update stock and create order in one transaction.",
                    "parameters": {
                        "medicine_name": {
                            "description": "Medicine name",
                            "required": True,
                            "type": "string",
                        },
                        "quantity": {
                            "description": "Requested quantity",
                            "required": True,
                            "type": "integer",
                        },
                    },
                    "requireConfirmation": "DISABLED",
                }
            ]
        },
    },
]


def find_agent_id_by_name(name: str):
    paginator = client.get_paginator("list_agents")
    for page in paginator.paginate():
        for summary in page.get("agentSummaries", []):
            if summary.get("agentName") == name:
                return summary["agentId"]
    return None


def get_or_create_agent():
    existing_agent_id = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                existing_agent_id = json.load(f).get("agent_id")
        except (json.JSONDecodeError, OSError):
            existing_agent_id = None

    if not existing_agent_id:
        existing_agent_id = find_agent_id_by_name(AGENT_NAME)

    if existing_agent_id:
        try:
            response = client.update_agent(
                agentId=existing_agent_id,
                agentName=AGENT_NAME,
                description="Executes approved create_order and update_inventory actions",
                instruction=INSTRUCTION,
                foundationModel=FOUNDATION_MODEL,
                agentResourceRoleArn=AGENT_ROLE_ARN,
            )
            return response["agent"]["agentId"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

    response = client.create_agent(
        agentName=AGENT_NAME,
        description="Executes approved create_order and update_inventory actions",
        instruction=INSTRUCTION,
        foundationModel=FOUNDATION_MODEL,
        agentResourceRoleArn=AGENT_ROLE_ARN,
    )
    return response["agent"]["agentId"]


def upsert_action_group(agent_id, group):
    action_groups = client.list_agent_action_groups(
        agentId=agent_id,
        agentVersion="DRAFT",
    ).get("actionGroupSummaries", [])

    existing = next((a for a in action_groups if a.get("actionGroupName") == group["name"]), None)

    payload = {
        "actionGroupName": group["name"],
        "agentId": agent_id,
        "agentVersion": "DRAFT",
        "description": f"Action group for {group['name']}",
        "actionGroupExecutor": {"lambda": group["lambda_arn"]},
        "functionSchema": group["function_schema"],
        "actionGroupState": "ENABLED",
    }

    if existing:
        response = client.update_agent_action_group(
            actionGroupId=existing["actionGroupId"],
            **payload,
        )
        return response["agentActionGroup"]["actionGroupId"]

    response = client.create_agent_action_group(**payload)
    return response["agentActionGroup"]["actionGroupId"]


agent_id = get_or_create_agent()
print(f"Upserted agent: {agent_id}")

while True:
    status = client.get_agent(agentId=agent_id)["agent"]["agentStatus"]
    print(f"Agent status: {status}")
    if status in {"NOT_PREPARED", "CREATED", "PREPARED", "FAILED"}:
        break
    time.sleep(5)

action_group_ids = {}
for group in ACTION_GROUPS:
    ag_id = upsert_action_group(agent_id, group)
    action_group_ids[group["name"]] = ag_id
    print(f"Upserted action group {group['name']}: {ag_id}")

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump({"agent_id": agent_id, "action_group_ids": action_group_ids}, f, indent=2)

print(f"Saved state: {STATE_FILE}")
