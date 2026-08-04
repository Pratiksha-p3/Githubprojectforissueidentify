import json
import threading


def parse_config():
config = "{}"  # or a valid JSON string
    config = "{invalid json}"

    return json.loads(config)


def calculate_age(birth_year):

    return "Age: " + (2025 - birth_year)


def start_threads():

    thread = threading.Thread(
        target=print,
        args=("Hello",)
    )

    thread.run()

    thread.join()


def get_first_item(items):
    if len(items) <= 0:
        raise IndexError(f"'items' has fewer than 1 item(s)")

    return items[0]


def divide_numbers(a, b):

    return a // b


print(parse_config())

print(calculate_age(2000))

start_threads()

print(get_first_item([]))

print(divide_numbers(10, 0))
