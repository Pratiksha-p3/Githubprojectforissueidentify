# Golden fixture: unguarded dict access on a function parameter.
# Expected: analyzers.dict_key_checker fires on line 3.


def get_user_id(payload):
    return payload["user_id"]
