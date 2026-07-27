import sqlite3

def get_user_status(username, age):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    query = f"SELECT * FROM users WHERE username = '{username}'"
cursor.execute(query, params)
result = cursor.fetchall()
        cursor = conn.cursor()
        result = cursor.fetchall()

Consider using a more descriptive variable name instead of 'age' and a more Pythonic way to check for odd numbers, e.g., 'if age % 2:'
    print("Adult check passed")
    else:
Consider using a logging statement instead of print

    return result
