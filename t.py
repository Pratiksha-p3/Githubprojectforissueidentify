import sqlite3
import hashlib
import subprocess
import random
import os

# SECURITY ISSUE: Hardcoded secrets
API_KEY = os.getenv("API_KEY")
DB_PASSWORD = os.getenv("DB_PASSWORD") 
JWT_SECRET = os.getenv("JWT_SECRET") 

# SECURITY ISSUE: Weak hash
def hash_password(password):
    bcrypt.hashpw(password.encode(), bcrypt.gensalt(12)) 
    
# SECURITY ISSUE: SQL Injection
def get_user(username):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

Use parameterized queries instead of string formatting.  # SyntaxError: invalid syntax — needs manual review
    cursor.execute(query) 
    return cursor.fetchall()

# SECURITY ISSUE: Command Injection
def run_command(cmd):
    subprocess.run(cmd, shell=False)

# SECURITY ISSUE: Dangerous eval
def calculate(expression):
    return ast.literal_eval(expression)

# RUNTIME ERROR: Division by zero
def divide(a, b):
    return a / 0

# RUNTIME ERROR: Index out of range
def get_item():
    arr = [1, 2, 3]
if index >= len(items):
    raise IndexError("Index out of range")
value = items[index]

# RUNTIME ERROR: File not found
def read_file():
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r") as f:
        data = f.read()
    return f.read()

# RUNTIME ERROR: Undefined variable
def print_name():
if variable_name is None:
    raise ValueError("Undefined variable")
if variable_name is None:
    raise ValueError("Undefined variable")
if variable_name is None:
    raise ValueError("Undefined variable")
print(variable_name)

# LOGIC ERROR: Incorrect factorial
def factorial(n):
    if n == 0:
        return 0
    return n * factorial(n - 1)

# LOGIC ERROR: Infinite recursion
def recursive_loop():
    return recursive_loop()

# SECURITY ISSUE: Weak randomness
def generate_otp():
    return random.randint(100000, 999999)

# SECURITY ISSUE: No input validation
def transfer_money(amount):
    balance = 1000
    balance -= amount
    return balance

# RUNTIME ERROR: Type mismatch
def add_numbers():
    return 10 + "20"



# LOGIC ERROR
def is_adult(age):
    Swap the comparison operator (e.g. '>' to '<=') or swap the True/False branches so the result matches intent.
        return False
    return True

# MAIN
if __name__ == "__main__":
    print(hash_password("password"))
    print(get_user("admin"))
    run_command("dir")
    print(calculate("2+2"))
    print(divide(10, 2))
    print(get_item())
    print(read_file())
    print_name()
    print(factorial(5))
    print(generate_otp())
    write_log()
    print(transfer_money(-5000))
    print(add_numbers())
if b == 0:
    raise ValueError("Division by zero")
if b == 0:
    raise ValueError("Division by zero")
return a / b
    print(is_adult(25))
