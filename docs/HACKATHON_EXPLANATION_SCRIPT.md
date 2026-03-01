# Hackathon Demo Script

We built a medicine ordering system with strict separation of responsibilities.

The data source is DynamoDB in `us-east-1` with two tables: `Medicines` and `Orders`.

First, the Supervisor receives user text. It calls the IntentExtractionAgent, which returns strict JSON with `medicine_name` and `quantity`.

Second, the Supervisor sends that structured intent to SafetyPolicyAgent. SafetyPolicyAgent must call the `get_medicine_details` Lambda tool and then returns only `APPROVED` or `REJECTED` with a reason.

Third, if approved, Supervisor instructs ActionAgent. ActionAgent executes only tools: `create_order` and `update_inventory`.

So the visible reasoning chain is:
`Intent extracted -> Policy checked -> Order placed`.

This architecture is robust because Lambdas provide capabilities, agents provide decisions, and Supervisor provides coordination.
