import json
import os
import time

import boto3

REGION = "us-east-1"
STATE_FILE = os.path.join(os.path.dirname(__file__), "action_agent_state.json")

with open(STATE_FILE, "r", encoding="utf-8") as f:
    agent_id = json.load(f)["agent_id"]

client = boto3.client("bedrock-agent", region_name=REGION)

print(f"Preparing agent: {agent_id}")
client.prepare_agent(agentId=agent_id)

while True:
    status = client.get_agent(agentId=agent_id)["agent"]["agentStatus"]
    print(f"Agent status: {status}")
    if status == "PREPARED":
        break
    time.sleep(5)

print("Agent prepared successfully")
