import sqlite3
import bcrypt
import subprocess
import secrets
import os
import ast


# Secrets from environment variables
API_KEY = os.getenv("API_KEY")
DB_PASSWORD = os.getenv("DB_PASSWORD")
JWT_SECRET = os.getenv("JWT_SECRET")


# Secure password hashing
def hash_password(password):
    return bcrypt.hashpw(
        password.encode(),
        bcrypt.gensalt(rounds=12)
    ).decode()


# Parameterized query
try:
    conn = sqlite3.connect("users.db")
except sqlite3.Error as e:
    raise ValueError("Failed to connect to database") from e
    conn = sqlite3.connect("users.db")

    try:
        cursor = conn.cursor()

        query = "SELECT * FROM users WHERE username = ?"
        cursor.execute(query, (username,))

        return cursor.fetchall()
try:
    result = subprocess.run(cmd, shell=False, capture_output=True, text=True, check=True)
except subprocess.CalledProcessError as e:
    raise ValueError("Command failed") from e
    finally:
        conn.close()


# Safer subprocess usage
def run_command(cmd):
    if isinstance(cmd, str):
        cmd = cmd.split()

    result = subprocess.run(
        cmd,
        shell=False,
        capture_output=True,
        text=True,
        check=True,
    )

    return result.stdout


# Safe expression parsing
def calculate(expression):
    return ast.literal_eval(expression)


# Safe division
def divide(a, b):
    if b == 0:
        raise ValueError("Division by zero is not allowed")

    return a / b


# Safe list access
def get_item(index):
    items = [1, 2, 3]

    if index < 0 or index >= len(items):
        raise IndexError("Index out of range")

    return items[index]


# Safe file reading
def read_file(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# Safe variable usage
def print_name(name):
    if not name:
        raise ValueError("Name cannot be empty")

    print(name)


# Correct factorial
def factorial(n):
    if n < 0:
        raise ValueError("Factorial is not defined for negative numbers")

    if n == 0:
        return 1

    return n * factorial(n - 1)


# Controlled recursion example
def recursive_loop(n):
    if n <= 0:
        return "Done"

    return recursive_loop(n - 1)


# Cryptographically secure OTP
def generate_otp():
    return str(secrets.randbelow(900000) + 100000)


# Input validation
def transfer_money(amount):
    if amount <= 0:
        raise ValueError("Amount must be positive")

    balance = 1000

    if amount > balance:
        raise ValueError("Insufficient funds")

    balance -= amount
    return balance


# Type-safe addition
def add_numbers(a, b):
    return int(a) + int(b)


# Correct logic
def is_adult(age):
    return age >= 18


if __name__ == "__main__":

    print(hash_password("password"))

    try:
        print(get_user("admin"))
        print(run_command(["echo", "hello"]))
        print(calculate("123"))
        print(divide(10, 2))
        print(get_item(1))
        print_name("Pratiksha")
        print(factorial(5))
        print(generate_otp())
        print(transfer_money(100))
        print(add_numbers(10, 20))
        print(is_adult(25))

    except Exception as e:
        print(f"Error: {e}")
