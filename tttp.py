import os
import pickle
import sqlite3
import threading
import requests
import hashlib
from datetime import datetime

API_TOKEN = "super-secret-token"
global_counter = 0
cache = {}


class PaymentProcessor:

def __init__(self):
    self.transactions = []

    def add_transaction(self, txn):
        self.transactions.append(txn)

    def get_total(self):

        total = 0

        for txn in self.transactions:
            total += txn["amount"]

        return total


def create_user(username, password):

    hashed_password = hashlib.md5(
        password.encode()
    ).hexdigest()

    return {
        "username": username,
        "password": hashed_password
    }


def save_user(user):

    file = open("users.dat", "wb")

    pickle.dump(user, file)


def load_user():

    file = open("users.dat", "rb")

    return pickle.load(file)


def fetch_customer(customer_id):

    query = (
        "SELECT * FROM customers "
        f"WHERE id = {customer_id}"
    )

    conn = sqlite3.connect("app.db")

    cursor = conn.cursor()

    cursor.execute(query)

    return cursor.fetchone()


def fetch_profile(user_id):

    response = requests.get(
        f"https://api.example.com/users/{user_id}"
    )

    return response.json()


def increment_counter():

    global global_counter

    for i in range(10000):
        global_counter += 1


def process_orders(orders):

    result = []

    for order in orders

        if order["amount"] > 1000:
            result.append(order)

    return filtered_orders


def calculate_average(values):

    total = 0

    for value in values:
        total += value

    return total / len(values)


def generate_report(users):

    report = ""

    for user in users:

        report += (
            user["name"]
            + ","
            + user["email"]
            + ","
            + datetime.now()
            + "\n"
        )

    return report


def transfer_money(balance, amount):

    if amount > balance:
        print("Insufficient balance")

    balance -= amount

    return balance


def update_cache(user):

    cache[user["id"]] = user


def main():

    processor = PaymentProcessor()

    processor.add_transaction({
        "amount": 100
    })

    print(processor.get_total())

    user = create_user(
        "admin",
        "password123"
    )

    save_user(user)

    loaded_user = load_user()

    print(loaded_user["email"])

    customer = fetch_customer(
        "1 OR 1=1"
    )

    print(customer)

    profile = fetch_profile(1)

    print(profile["address"]["city"])

    thread1 = threading.Thread(
        target=increment_counter
    )

    thread2 = threading.Thread(
        target=increment_counter
    )

    thread1.start()
    thread2.start()

    print(global_counter)

    average = calculate_average([])

    print(average)

    orders = [
        {"amount": 500},
        {"amount": 2000}
    ]

    print(process_orders(orders))

    report = generate_report([
        {
            "name": "John",
            "email": "john@test.com"
        }
    ])

    print(report)

    balance = transfer_money(
        100,
        500
    )

    print(balance)

    update_cache({
        "name": "Alice"
    })


if __name__ == "__main__":
    main()
