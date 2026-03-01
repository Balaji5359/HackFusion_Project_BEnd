import json
import os
import time

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
AGENT_NAME = "SupervisorAgent"
FOUNDATION_MODEL = "amazon.nova-lite-v1:0"
AGENT_ROLE_ARN = os.environ["BEDROCK_AGENT_ROLE_ARN"]
STATE_FILE = os.path.join(os.path.dirname(__file__), "supervisor_agent_state.json")

client = boto3.client("bedrock-agent", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)

INSTRUCTION = """
You are SupervisorAgent and the only user-facing orchestrator.

Mandatory flow:
1. Call IntentExtractionAgent collaborator to extract medicine_name and quantity JSON.
2. Send extracted intent to SafetyPolicyAgent collaborator for APPROVED/REJECTED decision.
3. If SafetyPolicyAgent returns REJECTED, stop and return a rejection explanation.
4. If APPROVED, instruct ActionAgent collaborator to execute inventory update first, then order creation.
5. Accept success only if ActionAgent confirms BOTH:
   - inventory_updated = true
   - order_created = true
   and includes order_id + new_stock evidence.
6. If either condition is false, return failure and explain database update did not complete.
5. Return final human-readable explanation with this chain:
   Intent extracted -> Policy checked -> Action executed.

Never skip steps and never execute database actions directly.
""".strip()

COLLABORATORS = [
    {
        "name": "intent_extractor",
        "state_path": os.path.join("intent-agent", "intent_agent_state.json"),
        "instruction": "Use this collaborator only to parse free text into strict JSON fields medicine_name and quantity.",
    },
    {
        "name": "safety_policy",
        "state_path": os.path.join("safety-agent", "safety_agent_state.json"),
        "instruction": "Use this collaborator to validate stock/prescription policy and return APPROVED or REJECTED with reason.",
    },
    {
        "name": "action_executor",
        "state_path": os.path.join("action-agent", "action_agent_state.json"),
        "instruction": "Use this collaborator only for execution after policy approval.",
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
                description="Orchestrates Intent, Safety, and Action collaborators",
                instruction=INSTRUCTION,
                foundationModel=FOUNDATION_MODEL,
                agentResourceRoleArn=AGENT_ROLE_ARN,
                agentCollaboration="SUPERVISOR",
            )
            return response["agent"]["agentId"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

    response = client.create_agent(
        agentName=AGENT_NAME,
        description="Orchestrates Intent, Safety, and Action collaborators",
        instruction=INSTRUCTION,
        foundationModel=FOUNDATION_MODEL,
        agentResourceRoleArn=AGENT_ROLE_ARN,
        agentCollaboration="SUPERVISOR",
    )
    return response["agent"]["agentId"]


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _alias_arn(account_id: str, agent_id: str, alias_id: str) -> str:
    return f"arn:aws:bedrock:{REGION}:{account_id}:agent-alias/{agent_id}/{alias_id}"


def upsert_collaborators(supervisor_agent_id: str):
    account_id = sts.get_caller_identity()["Account"]
    existing = client.list_agent_collaborators(
        agentId=supervisor_agent_id,
        agentVersion="DRAFT",
    ).get("agentCollaboratorSummaries", [])

    existing_by_name = {c["collaboratorName"]: c for c in existing}
    collaborator_ids = {}

    for collab in COLLABORATORS:
        state = _read_json(collab["state_path"])
        alias_arn = _alias_arn(account_id, state["agent_id"], state["agent_alias_id"])

        payload = {
            "agentDescriptor": {"aliasArn": alias_arn},
            "agentId": supervisor_agent_id,
            "agentVersion": "DRAFT",
            "collaborationInstruction": collab["instruction"],
            "collaboratorName": collab["name"],
            "relayConversationHistory": "TO_COLLABORATOR",
        }

        if collab["name"] in existing_by_name:
            collaborator_id = existing_by_name[collab["name"]]["collaboratorId"]
            try:
                client.update_agent_collaborator(
                    collaboratorId=collaborator_id,
                    **payload,
                )
            except ClientError as e:
                raise RuntimeError(
                    f"Failed to update collaborator '{collab['name']}' using alias {alias_arn}: {e}"
                ) from e
            collaborator_ids[collab["name"]] = collaborator_id
            print(f"Updated collaborator: {collab['name']} ({collaborator_id})")
        else:
            try:
                resp = client.associate_agent_collaborator(**payload)
            except ClientError as e:
                raise RuntimeError(
                    f"Failed to associate collaborator '{collab['name']}' using alias {alias_arn}: {e}"
                ) from e
            collaborator_id = resp["agentCollaborator"]["collaboratorId"]
            collaborator_ids[collab["name"]] = collaborator_id
            print(f"Associated collaborator: {collab['name']} ({collaborator_id})")

    return collaborator_ids


agent_id = get_or_create_agent()
print(f"Upserted supervisor agent: {agent_id}")

# Persist early so prepare/deploy/test have a stable state file even if collaborator association fails.
with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump({"agent_id": agent_id}, f, indent=2)
print(f"Saved initial state: {STATE_FILE}")

while True:
    status = client.get_agent(agentId=agent_id)["agent"]["agentStatus"]
    print(f"Agent status: {status}")
    if status in {"NOT_PREPARED", "CREATED", "PREPARED", "FAILED"}:
        break
    time.sleep(5)

collaborator_ids = upsert_collaborators(agent_id)

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump({"agent_id": agent_id, "collaborator_ids": collaborator_ids}, f, indent=2)

print(f"Saved state: {STATE_FILE}")
