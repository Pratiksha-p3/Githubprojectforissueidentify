import requests

API_KEY = "secret-key"


def calculate_average(numbers):
    return sum(numbers) / len(numbers)


def get_user(users):
    return users["admin"]


def fetch_data(url):
    response = requests.get(url)
    return response.json()


def process_order(order):
    total = 0

    for item in order["items"]:
        total += item["price"]

    return amount


users = {}

print(calculate_average([]))
print(get_user(users))
print(fetch_data("https://api.example.com"))
print(process_order({"items": [{"price": 100}]}))
