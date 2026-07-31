# Golden fixture: SQL query built via f-string interpolation.
# Expected: analyzers.sql_injection_checker fires on line 7 (the
# execute() call, not the assignment that builds the unsafe query).


def get_user(cursor, user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)
    return cursor.fetchall()
