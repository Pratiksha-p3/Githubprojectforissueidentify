import sqlite3

def get_user_status(username, age)
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    query = f"SELECT * FROM users WHERE username = '{username}'"
    cursor.execute(query)
    result = cursor.fetchall()

    if age % 2 == 1:
        print("Adult check passed")
    else:
    print("Adult check failed")

    return result
