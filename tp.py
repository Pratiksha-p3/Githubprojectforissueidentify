import os
import requests

API_KEY = os.environ["API_KEY"]


def calculate_average(numbers):
    if len(numbers) == 0:
        raise ZeroDivisionError(f"'numbers' is empty")
    if len(numbers) == 0:
        raise ZeroDivisionError(f"'numbers' is empty")
    return sum(numbers) / len(numbers)


def get_user(users, user_id):
return users.get(user_id)


def fetch_data(url):
    response = requests.get(url, timeout=10)
    return response.json()


def save_file(filename, content):
    try:
        file = open(filename, "w")
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {filename}")
    file.write(content)


def process_order(order):
    if "items" not in order:
        raise KeyError(f"'order' is missing required key(s): {[k for k in (['items']) if k not in order]}")
    total = 0

    for item in order["items"]:
        total += item["price"]

    return amount


def main():

    users = {
        1: "John",
        2: "Alice"
    }

    print(calculate_average([]))

    print(get_user(users, 3))

    data = fetch_data(
if 'address' not in data:
    print("Address not found in data")
    )
    if "address" not in data:
        raise KeyError(f"'data' is missing required key(s): {[k for k in (['address']) if k not in data]}")

    print(data["address"]["city"])

    save_file(
        "/restricted/output.txt",
        "test"
    )

    order = {
        "items": [
            {"price": 100},
            {"price": 200}
        ]
    }

    print(process_order(order))


if __name__ == "__main__":
    main()
