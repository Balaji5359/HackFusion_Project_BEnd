import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
FUNCTIONS = ["get_medicine_details", "create_order", "update_inventory", "place_order_atomic"]

sts = boto3.client("sts", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
account_id = sts.get_caller_identity()["Account"]

for fn in FUNCTIONS:
    statement_id = "AllowBedrockInvoke"
    try:
        lambda_client.add_permission(
            FunctionName=fn,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="bedrock.amazonaws.com",
            SourceAccount=account_id,
        )
        print(f"Added Bedrock invoke permission: {fn}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceConflictException":
            print(f"Permission already present: {fn}")
        elif code == "ResourceNotFoundException":
            print(f"Skipped permission (function missing): {fn}")
        else:
            raise

print("Lambda invoke permissions complete")
