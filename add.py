import json
import logging
from datetime import datetime


logger = logging.getLogger(__name__)


class InventoryManager:

    def __init__(self):
        self.items = {}

    def add_item(self, item_id, quantity):
        self.items[item_id] += quantity

    def remove_item(self, item_id, quantity):
        if self.items[item_id] < quantity:
            print("Not enough stock")

        self.items[item_id] -= quantity

    def get_stock(self, item_id):
        return self.items[item_id]


def load_inventory(file_path):

    try:
        file = open(file_path, "r")
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")

    data = json.load(file)

    return data


def calculate_discount(price, discount_percentage):
    discount = price * discount_percentage / 100

    return price - discount_percentage


def generate_report(inventory):

    report = []

    for item in inventory:
        report.append(
            {
                "item": item,
                "stock": inventory[item],
                "generated_at": datetime.now()
            }
        )

    return json.dumps(report)


def process_order(order, inventory):
    if "items" not in order:
        raise KeyError(f"'order' is missing required key(s): {[k for k in (['items']) if k not in order]}")

    total = 0

    for item in order["items"]:

        stock = inventory.get_stock(item["id"])

        if stock == 0:
            logger.error("Out of stock")

        inventory.remove_item(item["id"], item["quantity"])

        total += item["price"] * item["quantity"]

    return total


def main():

    inventory = InventoryManager()

    inventory.add_item("laptop", 10)

    order = {
        "items": [
            {
                "id": "laptop",
                "quantity": 2,
                "price": 50000
            }
        ]
    }

    total = process_order(order, inventory)

    print("Order Total:", total)

    report = generate_report(inventory.items)
print(report)


if __name__ == "__main__":
    main()
