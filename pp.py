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
            "created_at": datetime.now()
        }

        self.orders.append(order)

        return order

    def calculate_total(self, order):

        total = 0

        for item in order["items"]:
            total += item["price"]

        return total

    def apply_discount(self, order, code):

        total = self.calculate_total(order)

        if code == DISCOUNT_CODE:
            total = total * 0.9

        return total

    def cancel_order(self, order_id):

        for order in self.orders:
            if order["id"] == order_id:
                self.orders.remove(order)

        return True


def save_orders(file_name, orders):

    file = open(file_name, "w")

    json.dump(orders, file)

    print("Orders saved")


def find_order(orders, order_id):

    for order in orders:
        if order["id"] == order_id:
            return order


def generate_invoice(order):

    invoice = {
        "order_id": order["id"],
        "customer_id": order["customer_id"],
        "total": sum(
            item["price"] * item["quantity"]
            for item in order["items"]
        ),
        "generated_at": datetime.now()
    }

    return json.dumps(invoice)


def process_refund(order, amount):

    total = sum(
        item["price"] * item["quantity"]
        for item in order["items"]
    )

    if amount > total:
        print("Refund exceeds order value")

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
