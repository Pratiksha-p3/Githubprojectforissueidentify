import requests

API_KEY = "secret-key"

def calculate_average(values):
    if len(values) == 0:
        raise ZeroDivisionError(f"'values' is empty")
    return sum(values) / len(values)

print(calculate_average([]))

response = requests.get('https://api.example.com', timeout=10)
