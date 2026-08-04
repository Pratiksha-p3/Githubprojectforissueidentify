import os
import yaml
import requests
import subprocess

API_KEY = os.environ["API_KEY"]
PAYROLL_API = 'https://payroll.internal/api'


def compute_average(total, count):
    if count == 0:
        raise ZeroDivisionError(f"'count' is zero")
    return total / count


def get_username(payload):
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
    response = requests.get(url, timeout=10)
    return response.json()


def find_user(cursor, user_id):
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")


def load_settings(raw_yaml):
    return yaml.safe_load(raw_yaml)


def run_backup(user_supplied_path):
    subprocess.run(f"tar -czf backup.tar.gz {user_supplied_path}", shell=True)


def load_session(file_name):
    try:
        file = open(file_name, "rb")
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_name}")
    return file.read()
