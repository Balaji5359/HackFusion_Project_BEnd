import json
import os

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
TABLE_NAME = os.environ.get("MEDICINES_TABLE", "Medicines")

dynamodb = boto3.resource("dynamodb", region_name=REGION)
medicines = dynamodb.Table(TABLE_NAME)


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

    try:
        response = medicines.update_item(
            Key={"medicine_name": medicine_name},
            UpdateExpression="SET stock = stock - :q",
            ConditionExpression=Attr("stock").gte(quantity),
            ExpressionAttributeValues={":q": quantity},
            ReturnValues="UPDATED_NEW",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return _bedrock_response(
                event,
                {
                    "medicine_name": medicine_name,
                    "updated": False,
                    "reason": "INSUFFICIENT_STOCK",
                },
                409,
            )
        raise

    new_stock = int(response["Attributes"]["stock"])
    return _bedrock_response(
        event,
        {
            "medicine_name": medicine_name,
            "updated": True,
            "new_stock": new_stock,
        },
        200,
    )
