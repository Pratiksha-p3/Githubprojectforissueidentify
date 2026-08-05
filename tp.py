import threading
import json


def greet(name):
    return json.dumps({"message": f"hello {name}"})
