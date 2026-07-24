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
username = 'some_username'
cursor.execute(query, (username,))
        password.encode(),
        bcrypt.gensalt(rounds=12)
cmd = ['some_command']
result = subprocess.run(cmd, shell=False, capture_output=True, text=True, check=True)


# Parameterized query
try:
def run_command(cmd):
    try:
        return subprocess.run(cmd, shell=False, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise ValueError("Command failed") from e
except sqlite3.Error as e:
    raise ValueError("Failed to connect to database") from e
    conn = sqlite3.connect("users.db")

    try:
def calculate(expression):
    try:
        return ast.literal_eval(expression)
    except (SyntaxError, ValueError) as e:
        raise ValueError("Invalid expression") from e

        query = "SELECT * FROM users WHERE username = ?"
        cursor.execute(query, (username,))

        return cursor.fetchall()
try:  # SyntaxError: expected 'except' or 'finally' block — needs manual review
    result = subprocess.run(cmd, shell=False, capture_output=True, text=True, check=True)
except subprocess.CalledProcessError as e:
    raise ValueError("Command failed") from e
    finally:
        conn.close()

def get_item(index):
    items = [1, 2, 3]
    if index < 0 or index >= len(items):
        raise IndexError("Index out of range")
    return items[index]
# Safer subprocess usage
def run_command(cmd):
try:
    return ast.literal_eval(expression)
except (SyntaxError, ValueError) as e:
    raise ValueError("Invalid expression") from e
def read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(path)

    result = subprocess.run(
        cmd,
        shell=False,
        capture_output=True,
        text=True,
def print_name(name):
    if not isinstance(name, str):
        raise TypeError("Name must be a string")
    if not name:
        raise ValueError("Name cannot be empty")
    print(name)
    )
if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
    raise TypeError("Both a and b must be numbers")
if b == 0:
    raise ValueError("Division by zero")
if b == 0:
    raise ValueError("Division by zero")
return a / b
    return result.stdout

def factorial(n):
    if not isinstance(n, int):
        raise TypeError("Input must be an integer")
    if n < 0:
        raise ValueError("Factorial is not defined for negative numbers")
    if n == 0:
        return 1
    return n * factorial(n - 1)
# Safe expression parsing
def calculate(expression):
    return ast.literal_eval(expression)


# Safe division
def divide(a, b):
    if b == 0:
def recursive_loop(n):
    if not isinstance(n, int):
        raise TypeError("Input must be an integer")
    if n <= 0:
        return "Done"
    return recursive_loop(n - 1)
try:
if not os.path.exists(path):
    raise FileNotFoundError(path)
with open(path, "r") as f:
    data = f.read()
        return f.read()
except FileNotFoundError:
    raise FileNotFoundError(path)
def generate_otp():
    return str(secrets.randbelow(900000) + 100000)
    raise ValueError("Division by zero")
if b == 0:
    raise ValueError("Division by zero")
if b == 0:
    raise ValueError("Division by zero")
return a / b

def transfer_money(amount):
    if not isinstance(amount, (int, float)):
        raise TypeError("Amount must be a number")
    if amount <= 0:
        raise ValueError("Amount must be positive")
    balance = 1000
    if amount > balance:
        raise ValueError("Insufficient funds")
    balance -= amount
    return balance
# Safe list access
def get_item(index):
    items = [1, 2, 3]

    if index < 0 or index >= len(items):
if not isinstance(name, str):
    raise TypeError("Name must be a string")
if variable_name is None:
    raise ValueError("Undefined variable")
print(variable_name)

    return items[index]
def add_numbers(a, b):
    try:
        return int(a) + int(b)
    except ValueError:
        raise TypeError("Both a and b must be strings that can be converted to integers")

# Safe file reading
def read_file(path):
    if not os.path.exists(path):
def is_adult(age):
    if not isinstance(age, int):
        raise TypeError("Age must be an integer")
    return age >= 18

if not os.path.exists(path):
    raise FileNotFoundError(path)
if not os.path.exists(path):
    raise FileNotFoundError(path)
if not os.path.exists(path):
    raise FileNotFoundError(path)
with open(path, "r") as f:
    data = f.read()
    data = f.read()
    data = f.read()
        return f.read()


# Safe variable usage
def print_name(name):
    if not name:
        raise ValueError("Name cannot be empty")

if variable_name is None:
    raise ValueError("Undefined variable")
if variable_name is None:
    raise ValueError("Undefined variable")
if variable_name is None:
    raise ValueError("Undefined variable")
print(variable_name)

if not isinstance(amount, (int, float)):
    raise TypeError("Amount must be a number")
if amount <= 0:
    raise ValueError("Amount must be positive")
# Correct factorial
def factorial(n):
    if n < 0:
        raise ValueError("Factorial is not defined for negative numbers")

    if n == 0:
        return 1

    return n * factorial(n - 1)
try:
    return int(a) + int(b)
except ValueError:
    raise TypeError("Both a and b must be strings that can be converted to integers")

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

Catch specific exceptions instead of the broad `Exception`.
        print(f"Error: {e}")
