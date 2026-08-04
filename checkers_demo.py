import os
import requests


return total / count if count != 0 else 0
    if count == 0:
        raise ZeroDivisionError(f"'count' is zero")
    return total / count


return payload.get('username')
    if "username" not in payload:
        raise KeyError(f"'payload' is missing required key(s): {[k for k in (['username']) if k not in payload]}")
    return payload["username"]


def read_config(config_path):
    try:
        with open(config_path) as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {config_path}")


class Account:
    def __init__(self, owner):
        self.balance = 0
        self.owner = owner

    def describe(self):
        return f"Account owned by {self.owner}"


def fetch_data(url):
    response = requests.get(url)
    return response.json()


def find_user(cursor, user_id):
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")


API_KEY = "sk-live-abc123def456"
