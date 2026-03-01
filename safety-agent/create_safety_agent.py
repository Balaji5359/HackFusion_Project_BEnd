import json
import os
import time

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
AGENT_NAME = "SafetyPolicyAgent"
FOUNDATION_MODEL = "amazon.nova-lite-v1:0"
AGENT_ROLE_ARN = os.environ["BEDROCK_AGENT_ROLE_ARN"]
GET_MEDICINE_DETAILS_LAMBDA_ARN = os.environ["GET_MEDICINE_DETAILS_LAMBDA_ARN"]
STATE_FILE = os.path.join(os.path.dirname(__file__), "safety_agent_state.json")
ACTION_GROUP_NAME = "MedicineLookupActionGroup"

client = boto3.client("bedrock-agent", region_name=REGION)

INSTRUCTION = """
You are SafetyPolicyAgent.
You must validate medicine order requests and decide APPROVED or REJECTED.
Rules:
1. Always call get_medicine_details before deciding.
2. Reject if medicine is not found.
3. Reject if requested quantity is greater than stock.
4. Reject if requires_prescription is true and user did not explicitly provide a prescription.
5. Never place orders and never update inventory.
6. Return ONLY JSON with this schema:
{
  "decision": "APPROVED" | "REJECTED",
  "reason": "string",
  "medicine_name": "string|null",
  "requested_quantity": number
}
""".strip()

FUNCTION_SCHEMA = {
    "functions": [
        {
            "name": "get_medicine_details",
            "description": "Fetch current medicine availability and policy flags from DynamoDB.",
            "parameters": {
                "medicine_name": {
                    "description": "Exact medicine name",
                    "required": True,
                    "type": "string",
                }
            },
            "requireConfirmation": "DISABLED",
        }
    ]
}


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
                description="Validates stock and prescription policy before order execution",
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
        description="Validates stock and prescription policy before order execution",
        instruction=INSTRUCTION,
        foundationModel=FOUNDATION_MODEL,
        agentResourceRoleArn=AGENT_ROLE_ARN,
    )
    return response["agent"]["agentId"]


def upsert_action_group(agent_id: str):
    action_groups = client.list_agent_action_groups(
        agentId=agent_id,
        agentVersion="DRAFT",
    ).get("actionGroupSummaries", [])

    existing = next((a for a in action_groups if a.get("actionGroupName") == ACTION_GROUP_NAME), None)

    payload = {
        "actionGroupName": ACTION_GROUP_NAME,
        "agentId": agent_id,
        "agentVersion": "DRAFT",
        "description": "Reads medicine stock and prescription requirement",
        "actionGroupExecutor": {"lambda": GET_MEDICINE_DETAILS_LAMBDA_ARN},
        "functionSchema": FUNCTION_SCHEMA,
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

action_group_id = upsert_action_group(agent_id)
print(f"Upserted action group: {action_group_id}")

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump({"agent_id": agent_id, "action_group_id": action_group_id}, f, indent=2)

print(f"Saved state: {STATE_FILE}")
