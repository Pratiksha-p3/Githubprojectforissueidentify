import requests

API_KEY = "secret-key"


def calculate_average(numbers):
    if len(numbers) == 0:
        raise ZeroDivisionError(f"'numbers' is empty")
    return sum(numbers) / len(numbers)

return sum(numbers) / len(numbers) if numbers else 0
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
    return amount


print(calculate_average([]) if [] else 0)
print(get_user(users) if 'admin' in users else None)
print(calculate_average([]))
print(get_user(users))
print(fetch_data("https://api.example.com"))
print(process_order({"items": [{"price": 100}]}))
