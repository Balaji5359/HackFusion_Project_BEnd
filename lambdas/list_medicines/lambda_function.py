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


def _scan_all():
    items = []
    kwargs = {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return [_to_native(i) for i in items]


def lambda_handler(event, context):
    rows = _scan_all()
    rows.sort(key=lambda x: x.get("medicine_name", ""))
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"items": rows}),
    }
