import json
import os
import uuid
from datetime import datetime, timezone

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
ORDERS_TABLE = os.environ.get("ORDERS_TABLE", "Orders")

dynamodb = boto3.resource("dynamodb", region_name=REGION)
orders = dynamodb.Table(ORDERS_TABLE)


def _extract_param(event, key):
    if key in event:
        return event.get(key)

    for p in event.get("parameters", []):
        if p.get("name") == key:
            return p.get("value")

    if isinstance(event.get("parameters"), dict):
        return event["parameters"].get(key)

    return None


def _bedrock_response(event, body, status_code=200):
    if "actionGroup" in event and "function" in event:
        return {
            "messageVersion": "1.0",
            "response": {
                "actionGroup": event["actionGroup"],
                "function": event["function"],
                "functionResponse": {
                    "responseBody": {
                        "TEXT": {
                            "body": json.dumps(body)
                        }
                    }
                },
            },
            "sessionAttributes": event.get("sessionAttributes", {}),
            "promptSessionAttributes": event.get("promptSessionAttributes", {}),
        }

    return {"statusCode": status_code, "body": json.dumps(body)}


def lambda_handler(event, context):
    medicine_name = _extract_param(event, "medicine_name")
    quantity = int(_extract_param(event, "quantity") or 1)

    if not medicine_name or quantity <= 0:
        return _bedrock_response(
            event,
            {"error": "medicine_name and positive quantity are required"},
            400,
        )

    order_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    item = {
        "order_id": order_id,
        "medicine_name": medicine_name,
        "quantity": quantity,
        "status": "PLACED",
        "created_at": created_at,
    }
    orders.put_item(Item=item)

    return _bedrock_response(
        event,
        {
            "order_id": order_id,
            "medicine_name": medicine_name,
            "quantity": quantity,
            "status": "PLACED",
        },
        200,
    )
