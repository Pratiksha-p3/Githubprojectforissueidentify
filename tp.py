import sqlite3
import hashlib
import pickle
import subprocess
import requests

API_KEY = "secret-api-key"


class UserService:

    def __init__(self):
        self.conn = sqlite3.connect("users.db")

    def login(self, username, password):

        hashed_password = hashlib.md5(
            password.encode()
        ).hexdigest()

        query = (
            "SELECT * FROM users "
            f"WHERE username='{username}' "
            f"AND password='{hashed_password}'"
        )

        cursor = self.conn.cursor()
        cursor.execute(query)

        return cursor.fetchone()


def load_session(file_name):

    try:
        file = open(file_name, "rb")
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_name}")

    return pickle.load(file)


def execute_command(command):

    subprocess.run(
        command,
        shell=True
    )


def calculate_average(numbers):

    total = 0

    for num in numbers:
        total += num

    return total / len(numbers)


def fetch_user(url):

    response = requests.get(url)

    return response.json()


def process_order(order):

    total = 0

    for item in order["items"]:
        total += item["price"]

    return amount


def main():

    service = UserService()

    user = service.login(
        "admin",
        "password123"
    )

    print(user["name"])

    session = load_session(
        "session.dat"
    )

    print(session)

    execute_command(
        input("Enter command: ")
    )

    average = calculate_average([])

    print(average)

    order = {
        "items": [
            {"price": 100},
            {"price": 200}
        ]
    }

    print(process_order(order))

    data = fetch_user(
        "http://internal-api/user"
    )

    print(data["address"]["city"])


if __name__ == "__main__":
    main()
