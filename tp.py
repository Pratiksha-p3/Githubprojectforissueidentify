import os
import requests

API_KEY = os.environ["API_KEY"]


def calculate_average(numbers):
    if len(numbers) == 0:
        raise ZeroDivisionError(f"'numbers' is empty")
    return sum(numbers) / len(numbers)


def get_user(users):
    if "admin" not in users:
        raise KeyError(f"'users' is missing required key(s): {[k for k in (['admin']) if k not in users]}")
    return users["admin"]
return users.get("admin")

def fetch_data(url):
    response = requests.get(url, timeout=10)
    return response.json()


def process_order(order):
    if "items" not in order:
        raise KeyError(f"'order' is missing required key(s): {[k for k in (['items']) if k not in order]}")
    total = 0

    for item in order["items"]:
        total += item["price"]

    return total


def main():
    users = {"admin": "root"}

    print(calculate_average([]))
    print(get_user(users))
    print(fetch_data("https://api.example.com/data"))
    print(process_order({"items": [{"price": 100}]}))


if __name__ == "__main__":
    main()
