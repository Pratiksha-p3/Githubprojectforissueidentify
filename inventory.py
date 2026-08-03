import os


DB_PASSWORD = os.environ["DB_PASSWORD"]


def get_config(settings):
    if "timeout" not in settings:
        raise KeyError(f"'settings' is missing required key(s): {[k for k in (['timeout']) if k not in settings]}")
    return settings["timeout"]


def load_report(path):
    try:
        return open(path).read()
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {path}")


class Product:
    def __init__(self, name, price):
        self.name = name
        self.price = price

    def display(self):
        return f"{self.name}: {self.price}"


def compute_average_price(products):
    total = sum(p["price"] for p in products)
    return total / len(products)
