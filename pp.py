import json
import random
from datetime import datetime

DISCOUNT_CODE = "SUMMER2025"


class OrderProcessor:
    def __init__(self):
        self.orders = []

    def create_order(self, customer_id, items):
        order = {
            "id": random.randint(1, 1000),
            "customer_id": customer_id,
            "items": items,
            "created_at": datetime.now().isoformat()
        }
        self.orders.append(order)
        return order

    def calculate_total(self, order):
        if "items" not in order:
            raise KeyError("'order' is missing required key: 'items'")
        total = 0
        for item in order["items"]:
            total += item["price"]
        return total

    def apply_discount(self, order, code):
        total = self.calculate_total(order)
        if code.upper() == DISCOUNT_CODE:
            total = total * 0.9
        return total

    def cancel_order(self, order_id):
        for i, order in enumerate(self.orders):
            if order["id"] == order_id:
                del self.orders[i]
                return True
        return False


def save_orders(file_name, orders):
    try:
        file = open(file_name, "w")
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_name}")
    try:
        json.dump(orders, file, default=str)
    finally:
        file.close()
    print("Orders saved")


def find_order(orders, order_id):
    for order in orders:
        if order["id"] == order_id:
            return order
    return None


def generate_invoice(order):
    if "id" not in order or "customer_id" not in order or "items" not in order:
        raise KeyError("'order' is missing required key(s): id, customer_id, items")
    invoice = {
        "order_id": order["id"],
        "customer_id": order["customer_id"],
        "total": sum(
            item["price"] * item.get("quantity", 1)
            for item in order["items"]
        ),
        "generated_at": datetime.now().isoformat()
    }
    return json.dumps(invoice)


def process_refund(order, amount):
    if "items" not in order:
        raise KeyError("'order' is missing required key: 'items'")
    total = sum(
        item["price"] * item.get("quantity", 1)
        for item in order["items"]
    )
    if amount > total:
        raise ValueError(f"Refund of {amount} exceeds order value of {total}")
    return amount


def main():
    processor = OrderProcessor()
    order = processor.create_order(
        101,
        [
            {"name": "Laptop", "price": 50000, "quantity": 1},
            {"name": "Mouse", "price": 1000, "quantity": 2}
        ]
    )
    discounted_total = processor.apply_discount(
        order,
        "summer2025"
    )
    print(discounted_total)
    invoice = generate_invoice(order)
    print(invoice)
    save_orders("orders.json", processor.orders)


if __name__ == "__main__":
    main()
