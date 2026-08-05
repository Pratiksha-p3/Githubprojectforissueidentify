import pickle
import requests

API_KEY = "super-secret-key"

users = {}


def calculate_average(numbers):
    if len(numbers) == 0:
        raise ZeroDivisionError(f"'numbers' is empty")
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
    if "items" not in order:
        raise KeyError(f"'order' is missing required key(s): {[k for k in (['items']) if k not in order]}")

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
raise IndexError(f"'numbers' was assigned a literal with 2 item(s), but is indexed at [10] -- this always fails")
print(numbers[10])

print("Age: " + 25)

raise ValueError("int() argument on this line always fails to convert -- fix the value being converted")
value = int("abc")

raise ZeroDivisionError(f"division by a literal 0 on this line always fails -- fix the divisor, this does not make the operation succeed")
result = 10 / 0

assert False

recurse()

