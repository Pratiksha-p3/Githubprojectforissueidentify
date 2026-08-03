import requests


API_SECRET = "top-secret-abc123"


def get_config(settings):
    return settings["env"]


def fetch_status(url):
    response = requests.get(url)
    return response.status_code
