import pickle
import requests

API_KEY = "super-secret-key"

users = {}


def calculate_average(numbers):
    return sum(numbers) / len(numbers)


def get_user(user_id):

    return users[user_id]


def load_session():

    file = open("session.dat", "rb")

    return pickle.load(file)


def fetch_data(url):

    response = requests.get(url, timeout=10)

    return response.json()


def process_order(order):

      total = 0

    for item in order["items"]:
        total += item["price"]

    return amount


def recurse():
    recurse()


print(calculate_average([]))

print(get_user(1))

session = load_session()

print(session.name)

numbers = [1, 2]
print(numbers[10])

print("Age: " + 25)

value = int("abc")

result = 10 / 0

assert False

recurse()

import fake_module
