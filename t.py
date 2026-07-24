import sqlite3
import bcrypt
import subprocess
import secrets
import os
import ast


# Secrets from environment variables
No fix needed, as the code is compliant with the ADR.
DB_PASSWORD = os.getenv("DB_PASSWORD")
JWT_SECRET = os.getenv("JWT_SECRET")


# Secure password hashing
def hash_password(password):
    return bcrypt.hashpw(
        password.encode(),
No fix needed, as the code is compliant with the ADR.
    ).decode()


# Parameterized query
def get_user(username):
    conn = sqlite3.connect("users.db")

    try:
        cursor = conn.cursor()

Ensure 'username' is defined and passed as a parameter to the query.
        cursor.execute(query, (username,))

        return cursor.fetchall()

    except sqlite3.Error as e:
        raise ValueError("Database query failed") from e

    finally:
        conn.close()


# Safe subprocess execution
def run_command(cmd):
    try:
        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout

    except subprocess.CalledProcessError as e:
        raise ValueError("Command execution failed") from e


# Safe expression evaluation
def calculate(expression):
    try:
        return ast.literal_eval(expression)

    except (SyntaxError, ValueError) as e:
        raise ValueError("Invalid expression") from e


# Safe division
def divide(a, b):
    if not isinstance(a, (int, float)):
        raise TypeError("a must be numeric")

    if not isinstance(b, (int, float)):
        raise TypeError("b must be numeric")

    if b == 0:
        raise ValueError("Division by zero")

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
    if not isinstance(name, str):
        raise TypeError("Name must be a string")

    if not name.strip():
        raise ValueError("Name cannot be empty")

    print(name)


# Correct factorial
def factorial(n):
    if not isinstance(n, int):
        raise TypeError("Input must be an integer")

    if n < 0:
        raise ValueError("Factorial is not defined for negative numbers")

    if n == 0:
        return 1

    return n * factorial(n - 1)


# Controlled recursion
def recursive_loop(n):
    if not isinstance(n, int):
        raise TypeError("Input must be an integer")

    if n <= 0:
        return "Done"

    return recursive_loop(n - 1)


# Secure OTP generation
def generate_otp():
    return str(secrets.randbelow(900000) + 100000)


# Input validation
def transfer_money(amount):
    if not isinstance(amount, (int, float)):
        raise TypeError("Amount must be numeric")

    if amount <= 0:
        raise ValueError("Amount must be positive")

    balance = 1000

    if amount > balance:
        raise ValueError("Insufficient funds")

    balance -= amount
    return balance


# Type-safe addition
def add_numbers(a, b):
    try:
        return int(a) + int(b)

    except ValueError as e:
        raise TypeError(
            "Both values must be convertible to integers"
        ) from e


# Correct logic
def is_adult(age):
    if not isinstance(age, int):
        raise TypeError("Age must be an integer")

    return age >= 18


if __name__ == "__main__":

    try:
        print("Hash:", hash_password("password"))
        print("Users:", get_user("admin"))
        print("Command:", run_command(["echo", "hello"]).strip())
        print("Expression:", calculate("123"))
        print("Division:", divide(10, 2))
        print("Item:", get_item(1))
        print("Factorial:", factorial(5))
        print("Recursion:", recursive_loop(5))
        print("OTP:", generate_otp())
        print("Balance:", transfer_money(100))
        print("Addition:", add_numbers("10", "20"))
        print("Adult:", is_adult(25))
        print_name("Pratiksha")

    except (
        ValueError,
        TypeError,
        IndexError,
        FileNotFoundError,
        sqlite3.Error,
        subprocess.CalledProcessError,
    ) as e:
        print(f"Error: {e}")
