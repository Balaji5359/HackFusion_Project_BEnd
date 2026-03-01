import json
import os
import time

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
AGENT_NAME = "IntentExtractionAgent"
FOUNDATION_MODEL = "amazon.nova-lite-v1:0"
AGENT_ROLE_ARN = os.environ["BEDROCK_AGENT_ROLE_ARN"]
STATE_FILE = os.path.join(os.path.dirname(__file__), "intent_agent_state.json")

client = boto3.client("bedrock-agent", region_name=REGION)

INSTRUCTION = """
You are an intent extraction agent.

Rules:
1. Return ONLY valid JSON.
2. Do not add markdown, prose, or explanations.
3. Extract medicine_name and quantity from user text.
4. If quantity is missing, default to 1.
5. If medicine_name is unclear, set medicine_name to null.
6. If quantity is unclear, set quantity to 1.

Output schema:
{
  "medicine_name": "string or null",
  "quantity": 1
}
""".strip()


def find_agent_id_by_name(name: str):
    paginator = client.get_paginator("list_agents")
    for page in paginator.paginate():
        for summary in page.get("agentSummaries", []):
            if summary.get("agentName") == name:
                return summary["agentId"]
    return None


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
            description="Extracts medicine name and quantity from user input",
            instruction=INSTRUCTION,
            foundationModel=FOUNDATION_MODEL,
            agentResourceRoleArn=AGENT_ROLE_ARN,
        )
        agent_id = response["agent"]["agentId"]
        print(f"Updated agent: {agent_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            existing_agent_id = None
        else:
            raise

if not existing_agent_id:
    response = client.create_agent(
        agentName=AGENT_NAME,
        description="Extracts medicine name and quantity from user input",
        instruction=INSTRUCTION,
        foundationModel=FOUNDATION_MODEL,
        agentResourceRoleArn=AGENT_ROLE_ARN,
    )
    agent_id = response["agent"]["agentId"]
    print(f"Created agent: {agent_id}")

while True:
    status = client.get_agent(agentId=agent_id)["agent"]["agentStatus"]
    print(f"Agent status: {status}")
    if status in {"NOT_PREPARED", "CREATED", "PREPARED", "FAILED"}:
        break
    time.sleep(5)

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump({"agent_id": agent_id}, f, indent=2)
print(f"Saved state: {STATE_FILE}")
