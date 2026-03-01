import json
import os

import boto3

REGION = "us-east-1"
STATE_FILE = os.path.join(os.path.dirname(__file__), "supervisor_agent_state.json")

with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)

agent_id = state["agent_id"]
client = boto3.client("bedrock-agent", region_name=REGION)

aliases = client.list_agent_aliases(agentId=agent_id).get("agentAliasSummaries", [])
prod_alias = next((a for a in aliases if a["agentAliasName"] == "prod"), None)

if prod_alias:
    alias_id = prod_alias["agentAliasId"]
    client.update_agent_alias(
        agentAliasId=alias_id,
        agentAliasName="prod",
        agentId=agent_id,
        description="Production alias for SupervisorAgent",
    )
    print(f"Updated alias: {alias_id}")
else:
    response = client.create_agent_alias(
        agentId=agent_id,
        agentAliasName="prod",
        description="Production alias for SupervisorAgent",
    )
    alias_id = response["agentAlias"]["agentAliasId"]
    print(f"Created alias: {alias_id}")

state["agent_alias_id"] = alias_id

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2)

print(f"Saved alias in state file: {alias_id}")
