import json
import os
import uuid

import boto3

REGION = "us-east-1"
STATE_FILE = os.path.join(os.path.dirname(__file__), "safety_agent_state.json")

with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)

agent_id = state["agent_id"]
alias_id = state["agent_alias_id"]
session_id = str(uuid.uuid4())

client = boto3.client("bedrock-agent-runtime", region_name=REGION)

response = client.invoke_agent(
    agentId=agent_id,
    agentAliasId=alias_id,
    sessionId=session_id,
    inputText="Validate this order: medicine_name=Crocin, quantity=2, prescription_provided=false",
    enableTrace=True,
)

output = ""
trace_count = 0
for event in response["completion"]:
    if "chunk" in event:
        output += event["chunk"]["bytes"].decode("utf-8")
    if "trace" in event:
        trace_count += 1

print("Agent output:")
print(output)
print(f"Trace events captured: {trace_count}")
