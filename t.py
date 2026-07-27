import sqlite3

def get_user_status(username, age):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    query = f"SELECT * FROM users WHERE username = '{username}'"
cursor.execute(query, params)
result = cursor.fetchall()
        cursor = conn.cursor()
        result = cursor.fetchall()

if age % 2 == 1:
    print("Adult check passed")
    else:
Consider using a logging statement instead of print

    return result
