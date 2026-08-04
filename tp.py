import os
import requests

API_KEY = os.getenv("API_KEY")


def calculate_average(numbers):
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
        with open(filename, "w") as file:
            file.write(content)
    except OSError as e:
        raise OSError(f"Could not write to {filename}: {e}")


def process_order(order):
    if "items" not in order:
        raise KeyError(f"'order' is missing required key(s): {[k for k in (['items']) if k not in order]}")
    total = 0

    for item in order["items"]:
        total += item["price"]

    return total


def main():

    users = {
        1: "John",
        2: "Alice"
    }

    print(calculate_average([]))

    print(get_user(users, 3))

    data = fetch_data(
        "https://api.example.com/data"
    )
    if "address" not in data:
        raise KeyError(f"'data' is missing required key(s): {[k for k in (['address']) if k not in data]}")
    if "city" not in data["address"]:
        raise KeyError("'city' is missing from data['address']")

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
