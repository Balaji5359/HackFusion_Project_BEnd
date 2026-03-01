import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
MEDICINES_TABLE = os.environ.get("MEDICINES_TABLE", "Medicines")
ORDERS_TABLE = os.environ.get("ORDERS_TABLE", "Orders")

dynamodb = boto3.client("dynamodb", region_name=REGION)


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
    params = event.get("parameters")
    if isinstance(params, list):
        for p in params:
            if p.get("name") == key:
                return p.get("value")
    if isinstance(params, dict):
        return params.get(key)

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
    if "actionGroup" in event and "function" in event:
        return {
            "messageVersion": "1.0",
            "response": {
                "actionGroup": event["actionGroup"],
                "function": event["function"],
                "functionResponse": {
                    "responseBody": {"TEXT": {"body": json.dumps(body)}}
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
    quantity = int(_extract_param(event, "quantity") or 1)

    if not medicine_name or quantity <= 0:
        return _bedrock_response(
            event,
            {"error": "medicine_name and positive quantity are required"},
            400,
        )

    order_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        dynamodb.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": MEDICINES_TABLE,
                        "Key": {"medicine_name": {"S": medicine_name}},
                        "UpdateExpression": "SET stock = stock - :q",
                        "ConditionExpression": "attribute_exists(medicine_name) AND stock >= :q",
                        "ExpressionAttributeValues": {":q": {"N": str(quantity)}},
                        "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
                    }
                },
                {
                    "Put": {
                        "TableName": ORDERS_TABLE,
                        "Item": {
                            "order_id": {"S": order_id},
                            "medicine_name": {"S": medicine_name},
                            "quantity": {"N": str(quantity)},
                            "status": {"S": "PLACED"},
                            "created_at": {"S": created_at},
                        },
                    }
                },
            ]
        )

        med = dynamodb.get_item(
            TableName=MEDICINES_TABLE,
            Key={"medicine_name": {"S": medicine_name}},
            ConsistentRead=True,
        ).get("Item", {})
        new_stock = int(med.get("stock", {}).get("N", "0"))

        return _bedrock_response(
            event,
            {
                "execution_status": "SUCCESS",
                "inventory_updated": True,
                "order_created": True,
                "order_id": order_id,
                "new_stock": new_stock,
                "reason": "ATOMIC_TRANSACTION_COMMITTED",
            },
            200,
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "ClientError")
        return _bedrock_response(
            event,
            {
                "execution_status": "FAILED",
                "inventory_updated": False,
                "order_created": False,
                "order_id": None,
                "new_stock": None,
                "reason": code,
            },
            409,
        )
