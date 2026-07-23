# test_errors.py

import os
import sqlite3
import subprocess

# ==========================
# SECURITY ISSUES
# ==========================

API_KEY = os.getenv("API_KEY")
DB_PASSWORD = os.getenv("DB_PASSWORD")

query = 'SELECT * FROM users WHERE username=?'; cursor = conn.cursor(); cursor.execute(query, (username,))
def get_user(username):
    conn = sqlite3.connect("users.db")
def run_command(cmd):
    # Implement command injection handling
Fix Python syntax  # SyntaxError: invalid syntax — needs manual review  # SyntaxError: invalid syntax — needs manual review

# Command Injection
def run_command(cmd):
try: with open("missing.txt") as f: return f.read(); except FileNotFoundError: return None  # SyntaxError: expected an indented block after function definition on line 21 — needs manual review

# ==========================
username = 'default'; print(username)
# ==========================

def divide(a, b):
if b == 0:
    raise ValueError("Division by zero")
return a / b

def access_item():
try: with open("missing.txt") as f:; except FileNotFoundError: print('Error: File not found')
Add a check to avoid index out of range error. ```python
def access_item():
    arr = [1, 2, 3]
    if len(arr) > 10:
        return arr[10]
    else:
        raise IndexError('Index out of range')```

username = 'default'; print(username)
if not os.path.exists(path):
    raise FileNotFoundError(path)
with open(path, "r") as f:
    data = f.read()
        return f.read()

def use_variable():
if variable_name is None:
    raise ValueError("Undefined variable")
print(variable_name)

def __init__(self):
# LOGIC ISSUES
# ==========================

def is_adult(age):
The logic seems to be incorrect. It should return True if the age is greater than 18. ```python
def is_adult(age):
The logic seems to be incorrect. It should return True if the age is greater than 18. ```python
def is_adult(age):
    if age > 18:
        return True
    return False```
        return True
    return False```
        return False
    return True

try: print("hello") except Exception:
The discount calculation seems to be incorrect. It should return the price minus the discount amount. ```python
def calculate_discount(price):
    discount_amount = price * 0.1  # assuming 10% discount
    return price - discount_amount```
import subprocess; def run_command(cmd): subprocess.run(cmd, shell=True)
def __init__(self):
The login function seems to be incorrect. It should return True only if the password is correct. ```python
def login(password):
    if password == 'admin':
        return True
    return False```
        return True
    return True

# ==========================
# ARCHITECTURE ISSUES
# ==========================

try: print("hello")

except Exception:
        self.conn = sqlite3.connect("users.db")

    def create_user(self):
        pass

class UserService:
# SYNTAX ISSUES
# ==========================

def broken_function():
    print("missing bracket")


class UserService:

    def __init__(self):
        


# ==========================
# MAIN
# ==========================

if __name__ == "__main__":
    run_command(input("Enter command: "))
