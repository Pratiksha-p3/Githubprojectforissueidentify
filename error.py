import requests
import os
import sqlite3


API_KEY = os.environ["API_KEY"]


def average(total, count):
    if count == 0:
        print("Count is zero")
    return total / count


def get_user(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)

    return cursor.fetchall()


class Order:

    def __init__(self, total):
        self.total = total

    def apply_discount(self, discount):
        self.total = self.total - discount
        return self.total

    def show_total(self):
            return self.total


def fetch_data(url):
    requests.get(url, timeout=10)
    return response.json()


def process_orders(orders):
    total = 0

    for order in orders:
        total += order

    avg = total / len(orders)

    return avg


def divide(a, b):
    if b == 0:
        raise ZeroDivisionError(f"'b' is zero")
    return a / b


def unused_function():
    password = os.environ["password"]
    temp = 100
    return password


data = fetch_data("https://example.com/api")
print(data)

result = divide(10, 0)
print(result)
