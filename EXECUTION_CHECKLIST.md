# End-to-End Terminal Plan (4 Agents + Lambda + DynamoDB + UI)

Run from:
`C:\HackFusion3_Project\Agentic_AI_Folder`

## 1) One-time environment lock
```powershell
$env:AWS_REGION="us-east-1"
$env:AWS_DEFAULT_REGION="us-east-1"
$env:BEDROCK_AGENT_ROLE_ARN="arn:aws:iam::433448338709:role/AmazonBedrockExecutionRoleForAgents"
$env:LAMBDA_EXEC_ROLE_ARN="arn:aws:iam::433448338709:role/medicine-agent-lambda-exec-role"
aws sts get-caller-identity
```

## 2) Base tables and seed
```powershell
python infra/setup_dynamodb.py
python infra/seed_medicines.py
```

## 3) Import hackathon dataset (XLSX) into Medicines only
```powershell
python infra/import_hackfusion_dataset.py
```

## 4) (Optional) Extract problem PDF to text
```powershell
python infra/extract_problem_pdf.py
```
If it asks for `pypdf`:
```powershell
pip install pypdf
python infra/extract_problem_pdf.py
```

## 5) Lambda tool deployment
```powershell
python infra/deploy_lambdas.py
python infra/allow_bedrock_to_invoke_lambdas.py
python infra/test_lambdas.py
```

## 6) Export Lambda ARNs for action groups
```powershell
$env:GET_MEDICINE_DETAILS_LAMBDA_ARN=(aws lambda get-function --function-name get_medicine_details --region us-east-1 | ConvertFrom-Json).Configuration.FunctionArn
$env:CREATE_ORDER_LAMBDA_ARN=(aws lambda get-function --function-name create_order --region us-east-1 | ConvertFrom-Json).Configuration.FunctionArn
$env:UPDATE_INVENTORY_LAMBDA_ARN=(aws lambda get-function --function-name update_inventory --region us-east-1 | ConvertFrom-Json).Configuration.FunctionArn
```

## 7) Agent 1: IntentExtractionAgent
```powershell
python intent-agent/create_intent_agent.py
python intent-agent/prepare_intent_agent.py
python intent-agent/deploy_intent_agent.py
python intent-agent/test_intent_agent.py
```

## 8) Agent 2: SafetyPolicyAgent
```powershell
python safety-agent/create_safety_agent.py
python safety-agent/prepare_safety_agent.py
python safety-agent/deploy_safety_agent.py
python safety-agent/test_safety_agent.py
```

## 9) Agent 3: ActionAgent
```powershell
python action-agent/create_action_agent.py
python action-agent/prepare_action_agent.py
python action-agent/deploy_action_agent.py
python action-agent/test_action_agent.py
```

## 10) Agent 4: SupervisorAgent
```powershell
python supervisor-agent/create_supervisor_agent.py
python supervisor-agent/prepare_supervisor_agent.py
python supervisor-agent/deploy_supervisor_agent.py
python supervisor-agent/test_supervisor_agent.py
```

## 11) Observability table for UI
```powershell
python infra/setup_observability_table.py
```

## 12) UI dependencies and launch
```powershell
pip install -r ui/requirements.txt
streamlit run ui/app.py
```

## 13) Observability checks
```powershell
aws logs tail /aws/lambda/get_medicine_details --follow --region us-east-1
aws logs tail /aws/lambda/create_order --follow --region us-east-1
aws logs tail /aws/lambda/update_inventory --follow --region us-east-1
```

## 14) DB verification after supervisor/UI runs
```powershell
python infra/verify_data_layer.py
aws dynamodb scan --table-name Orders --region us-east-1
aws dynamodb get-item --table-name Medicines --key '{"medicine_name":{"S":"Crocin"}}' --region us-east-1
```
