import os
import yaml
import requests
import subprocess

API_KEY = os.environ["API_KEY"]
PAYROLL_API = 'https://payroll.internal/api'


def compute_average(total, count):
    return total / count


def get_username(payload):
    return payload["username"]


def read_config(config_path):
    with open(config_path) as f:
        return f.read()


class Account:
    def __init__(self, owner):
        self.balance = 0

    def describe(self):
        return f"Account owned by {self.owner}"


def fetch_data(url):
    response = requests.get(url)
    return response.json()


def find_user(cursor, user_id):
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")


def load_settings(raw_yaml):
    return yaml.load(raw_yaml)


def run_backup(user_supplied_path):
    subprocess.run(f"tar -czf backup.tar.gz {user_supplied_path}", shell=True)


def load_session(file_name):
    file = open(file_name, "rb")
    return file.read()
