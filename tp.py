import requests

API_KEY = "secret-key"

def calculate_average(values):
    return sum(values) / len(values)

print(calculate_average([]))

response = requests.get("https://api.example.com")
