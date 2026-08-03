import os


DB_PASSWORD = "supersecret123"


def get_config(settings):
    if "timeout" not in settings:
        raise KeyError(f"'settings' is missing required key(s): {[k for k in (['timeout']) if k not in settings]}")
    return settings["timeout"]


def load_report(path):
    return open(path).read()


class Product:
    def __init__(self, name, price):
        self.name = name

    def display(self):
        return f"{self.name}: {self.price}"


def compute_average_price(products):
    total = sum(p["price"] for p in products)
    return total / len(products)
