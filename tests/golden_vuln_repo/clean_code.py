# Golden fixture with NO planted bugs -- the negative control. Every
# checker must report zero findings here; a checker that fires on this
# file is producing a false positive.

import requests


class Account:
    def __init__(self, owner, balance):
        self.owner = owner
        self.balance = balance

    def describe(self):
        return f"{self.owner}: {self.balance}"


def safe_divide(total, count):
    if count == 0:
        raise ValueError("count is zero")
    return total / count


def safe_lookup(payload):
    if "user_id" not in payload:
        raise KeyError("missing user_id")
    return payload["user_id"]


def safe_load(path):
    try:
        return open(path).read()
    except FileNotFoundError:
        return None


def safe_fetch(url):
    return requests.get(url, timeout=10)
