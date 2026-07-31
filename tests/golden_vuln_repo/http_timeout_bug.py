# Golden fixture: requests call with no timeout.
# Expected: analyzers.http_timeout_checker fires.

import requests


def fetch_price(url):
    return requests.get(url)
