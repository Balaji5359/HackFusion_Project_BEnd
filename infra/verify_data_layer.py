import boto3

REGION = "us-east-1"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
medicines = dynamodb.Table("Medicines")
orders = dynamodb.Table("Orders")

medicine = medicines.get_item(Key={"medicine_name": "Crocin"}).get("Item")
print("Medicine read:", medicine)

orders.put_item(Item={"order_id": "TEST-001", "medicine_name": "Crocin", "quantity": 1, "status": "TEST"})
order = orders.get_item(Key={"order_id": "TEST-001"}).get("Item")
print("Order write/read:", order)
