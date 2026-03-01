import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"

dynamodb = boto3.client("dynamodb", region_name=REGION)

TABLES = [
    {
        "TableName": "Medicines",
        "KeySchema": [{"AttributeName": "medicine_name", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "medicine_name", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "Orders",
        "KeySchema": [{"AttributeName": "order_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "order_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
]

for spec in TABLES:
    table_name = spec["TableName"]
    try:
        dynamodb.describe_table(TableName=table_name)
        print(f"Table already exists: {table_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        print(f"Creating table: {table_name}")
        dynamodb.create_table(**spec)
        waiter = dynamodb.get_waiter("table_exists")
        waiter.wait(TableName=table_name)
        print(f"Table active: {table_name}")

print("DynamoDB setup complete")
