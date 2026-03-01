import io
import os
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
RUNTIME = "python3.12"
TIMEOUT = 30

LAMBDA_ROLE_ARN = os.environ["LAMBDA_EXEC_ROLE_ARN"]
if "<" in LAMBDA_ROLE_ARN or ">" in LAMBDA_ROLE_ARN:
    raise ValueError(
        "LAMBDA_EXEC_ROLE_ARN is still a placeholder. "
        "Set a real IAM role ARN, e.g. arn:aws:iam::<account-id>:role/<lambda-exec-role>."
    )

FUNCTIONS = [
    {
        "name": "get_medicine_details",
        "source": os.path.join("lambdas", "get_medicine_details", "lambda_function.py"),
        "env": {"MEDICINES_TABLE": "Medicines"},
    },
    {
        "name": "create_order",
        "source": os.path.join("lambdas", "create_order", "lambda_function.py"),
        "env": {"ORDERS_TABLE": "Orders"},
    },
    {
        "name": "update_inventory",
        "source": os.path.join("lambdas", "update_inventory", "lambda_function.py"),
        "env": {"MEDICINES_TABLE": "Medicines"},
    },
    {
        "name": "place_order_atomic",
        "source": os.path.join("lambdas", "place_order_atomic", "lambda_function.py"),
        "env": {"MEDICINES_TABLE": "Medicines", "ORDERS_TABLE": "Orders"},
    },
    {
        "name": "list_orders",
        "source": os.path.join("lambdas", "list_orders", "lambda_function.py"),
        "env": {"ORDERS_TABLE": "Orders"},
    },
    {
        "name": "list_medicines",
        "source": os.path.join("lambdas", "list_medicines", "lambda_function.py"),
        "env": {"MEDICINES_TABLE": "Medicines"},
    },
]

client = boto3.client("lambda", region_name=REGION)


def _zip_file(path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, arcname="lambda_function.py")
    return buf.getvalue()


def _wait_for_ready(function_name, timeout_s=300):
    start = time.time()
    while True:
        cfg = client.get_function_configuration(FunctionName=function_name)
        state = cfg.get("State")
        status = cfg.get("LastUpdateStatus")

        if state == "Active" and status in {None, "Successful"}:
            return
        if status == "Failed":
            raise RuntimeError(
                f"Lambda update failed for {function_name}: {cfg.get('LastUpdateStatusReason')}"
            )
        if time.time() - start > timeout_s:
            raise TimeoutError(f"Timed out waiting for Lambda ready: {function_name}")
        time.sleep(4)


def _retry_conflict(function_name, operation, attempts=15):
    for i in range(attempts):
        try:
            return operation()
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceConflictException":
                raise
            if i == attempts - 1:
                raise
            _wait_for_ready(function_name)
            time.sleep(2)


for fn in FUNCTIONS:
    name = fn["name"]
    code = _zip_file(fn["source"])

    try:
        client.get_function(FunctionName=name)
        exists = True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            exists = False
        else:
            raise

    if not exists:
        resp = _retry_conflict(
            name,
            lambda: client.create_function(
                FunctionName=name,
                Runtime=RUNTIME,
                Role=LAMBDA_ROLE_ARN,
                Handler="lambda_function.lambda_handler",
                Code={"ZipFile": code},
                Timeout=TIMEOUT,
                Environment={"Variables": fn["env"]},
                Publish=True,
            ),
        )
        print(f"Created Lambda: {name} ({resp['FunctionArn']})")
        _wait_for_ready(name)
    else:
        _retry_conflict(
            name,
            lambda: client.update_function_code(FunctionName=name, ZipFile=code, Publish=True),
        )
        _wait_for_ready(name)
        _retry_conflict(
            name,
            lambda: client.update_function_configuration(
                FunctionName=name,
                Runtime=RUNTIME,
                Role=LAMBDA_ROLE_ARN,
                Handler="lambda_function.lambda_handler",
                Timeout=TIMEOUT,
                Environment={"Variables": fn["env"]},
            ),
        )
        _wait_for_ready(name)
        print(f"Updated Lambda: {name}")

print("Lambda deployment complete")
