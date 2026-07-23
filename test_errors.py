password = "admin123"   # Hardcoded secret

def divide(a, b):
    return a / 0        # Runtime Error: ZeroDivisionError

def factorial(n):
    if n == 0:
        return 0        # Logic Error
    return n * factorial(n - 1)

def get_user(username):
    query = f"SELECT * FROM users WHERE username='{username}'"  # SQL Injection
    return query

def run_command(cmd):
    import subprocess
    subprocess.run(cmd, shell=True)  # Command Injection Risk

def read_file():
    with open("missing.txt") as f:   # FileNotFoundError
        return f.read()

def get_item():
    arr = [1, 2, 3]
    return arr[10]                   # IndexError

def print_name():
    print(username)                  # NameError (undefined variable)

def parse_input(user_input):
    return eval(user_input)          # Dangerous eval()

class UserService
    def __init__(self):              # Syntax Error (missing :)
        pass
