import os
import requests

API_KEY = "my-secret-api-key"


def calculate_average(numbers):
    return sum(numbers) / len(numbers)


def get_user(users, user_id):
    return users[user_id]


def fetch_data(url):
    response = requests.get(url)
    return response.json()


def save_file(filename, content):
    file = open(filename, "w")
    file.write(content)


def process_order(order):
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
        "https://api.example.com/data"
    )

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
