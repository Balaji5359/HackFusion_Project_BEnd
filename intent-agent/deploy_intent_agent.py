import json
import os

import boto3

REGION = "us-east-1"
STATE_FILE = os.path.join(os.path.dirname(__file__), "intent_agent_state.json")

with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)

agent_id = state["agent_id"]
client = boto3.client("bedrock-agent", region_name=REGION)

versions = client.list_agent_versions(agentId=agent_id).get("agentVersionSummaries", [])
numbered_versions = []
for v in versions:
    value = v.get("agentVersion")
    if value and value.isdigit():
        numbered_versions.append(int(value))

latest_numbered = str(max(numbered_versions)) if numbered_versions else None

aliases = client.list_agent_aliases(agentId=agent_id).get("agentAliasSummaries", [])
prod_alias = next((a for a in aliases if a["agentAliasName"] == "prod"), None)

base_payload = {
    "agentAliasName": "prod",
    "agentId": agent_id,
    "description": "Production alias for IntentExtractionAgent",
}

if latest_numbered:
    base_payload["routingConfiguration"] = [{"agentVersion": latest_numbered}]
    print(f"Using numeric agent version: {latest_numbered}")
else:
    print("No numeric agent version found; creating/updating alias without routingConfiguration (DRAFT fallback).")

if prod_alias:
    alias_id = prod_alias["agentAliasId"]
    client.update_agent_alias(agentAliasId=alias_id, **base_payload)
    print(f"Updated existing alias: {alias_id}")
else:
    response = client.create_agent_alias(**base_payload)
    alias_id = response["agentAlias"]["agentAliasId"]
    print(f"Created alias: {alias_id}")

state["agent_alias_id"] = alias_id

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2)

print(f"Saved alias in state file: {alias_id}")
