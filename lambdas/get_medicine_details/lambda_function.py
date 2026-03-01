import json
import os
from decimal import Decimal

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
TABLE_NAME = os.environ.get("MEDICINES_TABLE", "Medicines")

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)


def _to_native(value):
    if isinstance(value, list):
        return [_to_native(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_native(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    return value


def _body_json(event):
    body = event.get("body")
    if not body:
        return {}
    if isinstance(body, dict):
        return body
    try:
        return json.loads(body)
    except Exception:
        return {}


def _extract_param(event, key):
    if key in event:
        return event.get(key)

    for p in event.get("parameters", []):
        if p.get("name") == key:
            return p.get("value")

    if isinstance(event.get("parameters"), dict):
        return event["parameters"].get(key)

    q = event.get("queryStringParameters") or {}
    if key in q:
        return q.get(key)

    p = event.get("pathParameters") or {}
    if key in p:
        return p.get(key)

    b = _body_json(event)
    if key in b:
        return b.get(key)

    return None


def _bedrock_response(event, body, status_code=200):
    # Bedrock action group function response envelope.
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

    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    medicine_name = _extract_param(event, "medicine_name")

    if not medicine_name:
        return _bedrock_response(event, {"error": "medicine_name is required"}, 400)

    item = table.get_item(Key={"medicine_name": medicine_name}).get("Item")
    if not item:
        return _bedrock_response(
            event,
            {"medicine_name": medicine_name, "found": False},
            404,
        )

    payload = _to_native(item)
    payload["found"] = True
    return _bedrock_response(event, payload, 200)
