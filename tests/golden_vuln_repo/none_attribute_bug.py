# Golden fixture: attribute access on a dict.get() result with no None check.
# Expected: analyzers.none_attribute_checker fires.


def get_role(payload):
    user = payload.get("user")
    return user.role
