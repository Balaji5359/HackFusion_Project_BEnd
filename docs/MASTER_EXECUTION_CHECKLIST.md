# Master Execution Checklist

This is the exact execution order for your architecture.

## 1) Region + credentials lock (one time)
```powershell
aws configure set region us-east-1
aws configure set output json
$env:AWS_REGION="us-east-1"
$env:AWS_DEFAULT_REGION="us-east-1"
```

## 2) Bedrock Agent role (one time)
Set the role ARN once in your shell:
```powershell
$env:BEDROCK_AGENT_ROLE_ARN="arn:aws:iam::<ACCOUNT_ID>:role/AmazonBedrockExecutionRoleForAgents"
```
Role must allow Bedrock Agents, Lambda invoke, CloudWatch Logs, and pass role.

## 3) Data layer first (DynamoDB)
```powershell
python infra/setup_dynamodb.py
python infra/seed_medicines.py
python infra/verify_data_layer.py
```

## 4) Tool layer (Lambda code)
Code locations:
- `lambdas/get_medicine_details/lambda_function.py`
- `lambdas/create_order/lambda_function.py`
- `lambdas/update_inventory/lambda_function.py`

Package + deploy each Lambda from this folder after creating IAM role for Lambda execution:
```powershell
Compress-Archive -Path lambdas/get_medicine_details/lambda_function.py -DestinationPath get_medicine_details.zip -Force
Compress-Archive -Path lambdas/create_order/lambda_function.py -DestinationPath create_order.zip -Force
Compress-Archive -Path lambdas/update_inventory/lambda_function.py -DestinationPath update_inventory.zip -Force
```

Then create/update Lambda functions in `us-east-1` (replace role ARN):
```powershell
aws lambda create-function --function-name get_medicine_details --runtime python3.12 --role arn:aws:iam::<ACCOUNT_ID>:role/<LAMBDA_ROLE> --handler lambda_function.lambda_handler --zip-file fileb://get_medicine_details.zip --timeout 30 --region us-east-1
aws lambda create-function --function-name create_order --runtime python3.12 --role arn:aws:iam::<ACCOUNT_ID>:role/<LAMBDA_ROLE> --handler lambda_function.lambda_handler --zip-file fileb://create_order.zip --timeout 30 --region us-east-1
aws lambda create-function --function-name update_inventory --runtime python3.12 --role arn:aws:iam::<ACCOUNT_ID>:role/<LAMBDA_ROLE> --handler lambda_function.lambda_handler --zip-file fileb://update_inventory.zip --timeout 30 --region us-east-1
```

## 5) Intent agent (already scaffolded)
```powershell
python intent-agent/create_intent_agent.py
python intent-agent/prepare_intent_agent.py
python intent-agent/deploy_intent_agent.py
python intent-agent/test_intent_agent.py
```

## 6) SafetyPolicyAgent
Create with `amazon.titan-text-lite-v1`, attach only `get_medicine_details` action group, and force decision output:
- `decision`: `APPROVED` or `REJECTED`
- `reason`: string
- always call tool before deciding.

## 7) ActionAgent
Create with same model; attach only `create_order` and `update_inventory` tools. No validation logic in prompt.

## 8) SupervisorAgent
Create final orchestrator with collaborators:
- IntentExtractionAgent
- SafetyPolicyAgent
- ActionAgent

Flow: extract intent -> validate policy -> execute action if approved -> explain chain.

## 9) Observability
- Enable CloudWatch Logs for Lambda.
- Use Bedrock agent trace in runtime tests.

## 10) Demo script
Say and show: `Intent extracted -> Policy checked -> Order placed`.
