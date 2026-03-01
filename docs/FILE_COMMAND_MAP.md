# File and Command Map

## 1. Region and env
- Set shell env and AWS profile before everything.

## 2. Data layer
- `infra/setup_dynamodb.py`
  - `python infra/setup_dynamodb.py`
- `infra/seed_medicines.py`
  - `python infra/seed_medicines.py`
- `infra/verify_data_layer.py`
  - `python infra/verify_data_layer.py`

## 3. Lambda tool layer
- `lambdas/get_medicine_details/lambda_function.py`
- `lambdas/create_order/lambda_function.py`
- `lambdas/update_inventory/lambda_function.py`

Package command examples:
- `Compress-Archive -Path lambdas/get_medicine_details/lambda_function.py -DestinationPath get_medicine_details.zip -Force`
- `Compress-Archive -Path lambdas/create_order/lambda_function.py -DestinationPath create_order.zip -Force`
- `Compress-Archive -Path lambdas/update_inventory/lambda_function.py -DestinationPath update_inventory.zip -Force`

Deploy command examples are in:
- `docs/MASTER_EXECUTION_CHECKLIST.md`

## 4. Intent agent lifecycle
- `intent-agent/create_intent_agent.py`
  - `python intent-agent/create_intent_agent.py`
- `intent-agent/prepare_intent_agent.py`
  - `python intent-agent/prepare_intent_agent.py`
- `intent-agent/deploy_intent_agent.py`
  - `python intent-agent/deploy_intent_agent.py`
- `intent-agent/test_intent_agent.py`
  - `python intent-agent/test_intent_agent.py`

## 5. Remaining agents
- SafetyPolicyAgent: create in Bedrock with only `get_medicine_details` action group.
- ActionAgent: create in Bedrock with only `create_order` and `update_inventory` action groups.
- SupervisorAgent: orchestrates all three agents.
