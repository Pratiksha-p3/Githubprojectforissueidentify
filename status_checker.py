import os
import requests


API_SECRET = os.environ["API_SECRET"]


def get_config(settings):
    if "env" not in settings:
        raise KeyError(f"'settings' is missing required key(s): {[k for k in (['env']) if k not in settings]}")
    return settings["env"]


def fetch_status(url):
    response = requests.get(url, timeout=10)
    return response.status_code
