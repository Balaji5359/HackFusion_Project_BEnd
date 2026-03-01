from decimal import Decimal

import boto3

REGION = "us-east-1"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table("Medicines")

seed_data = [
    {"medicine_name": "Crocin", "stock": 120, "requires_prescription": False, "price": Decimal("2.5")},
    {"medicine_name": "Azithromycin", "stock": 35, "requires_prescription": True, "price": Decimal("18.0")},
    {"medicine_name": "Dolo650", "stock": 80, "requires_prescription": False, "price": Decimal("3.0")},
    {"medicine_name": "Metformin", "stock": 50, "requires_prescription": True, "price": Decimal("12.0")},
    {"medicine_name": "Cetirizine", "stock": 75, "requires_prescription": False, "price": Decimal("4.0")},
]

for item in seed_data:
    table.put_item(Item=item)
    print(f"Upserted: {item['medicine_name']}")

print("Medicines seed complete")
