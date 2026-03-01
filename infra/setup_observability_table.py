import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
TABLE_NAME = "AgentRuns"

client = boto3.client("dynamodb", region_name=REGION)

spec = {
    "TableName": TABLE_NAME,
    "KeySchema": [{"AttributeName": "run_id", "KeyType": "HASH"}],
    "AttributeDefinitions": [{"AttributeName": "run_id", "AttributeType": "S"}],
    "BillingMode": "PAY_PER_REQUEST",
}

try:
    client.describe_table(TableName=TABLE_NAME)
    print(f"Table already exists: {TABLE_NAME}")
except ClientError as e:
    if e.response["Error"]["Code"] != "ResourceNotFoundException":
        raise
    client.create_table(**spec)
    client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
    print(f"Created table: {TABLE_NAME}")

print("Observability table setup complete")
