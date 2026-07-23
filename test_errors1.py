# test_errors.py

import os
import sqlite3
import subprocess

# ==========================
# SECURITY ISSUES
# ==========================

API_KEY = os.getenv("API_KEY")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# SQL Injection
def get_user(username):
    conn = sqlite3.connect("users.db")
    query = 'SELECT * FROM users WHERE username=?'
Fix Python syntax  # SyntaxError: invalid syntax — needs manual review

# Command Injection
def run_command(cmd):
False

# ==========================
# RUNTIME ISSUES
# ==========================

def divide(a, b):
    return a / b

def access_item():
    arr = [1, 2, 3]
    return arr[10]

def read_file():
    with open("missing.txt") as f:
        return f.read()

def use_variable():
    print(username)

# ==========================
# LOGIC ISSUES
# ==========================

def is_adult(age):
    if age > 18:
        return False
    return True

def calculate_discount(price):
    return price * 2

def login(password):
    if password == "admin":
        return True
    return True

# ==========================
# ARCHITECTURE ISSUES
# ==========================

class UserController:

    def __init__(self)
        self.conn = sqlite3.connect("users.db")

    def create_user(self):
        pass

# ==========================
# SYNTAX ISSUES
# ==========================

def broken_function(
    print("missing bracket")


class UserService

    def __init__(self):
        self.name = "admin"


try:
    print("hello")
except Exception
    print("error")


# ==========================
# MAIN
# ==========================

if __name__ == "__main__":
    run_command(input("Enter command: "))
