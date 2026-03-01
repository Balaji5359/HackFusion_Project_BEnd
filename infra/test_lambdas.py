import json

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
client = boto3.client("lambda", region_name=REGION)

TESTS = [
    ("get_medicine_details", {"medicine_name": "Crocin"}),
    ("create_order", {"medicine_name": "Crocin", "quantity": 1}),
    ("update_inventory", {"medicine_name": "Crocin", "quantity": 1}),
    ("place_order_atomic", {"medicine_name": "Crocin", "quantity": 1}),
]

for name, payload in TESTS:
    try:
        resp = client.invoke(
            FunctionName=name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        body = resp["Payload"].read().decode("utf-8")
        print(f"{name}: {body}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"{name}: SKIPPED (function not found)")
        else:
            raise
