import os

import requests
import sqlite3


API_TOKEN = os.environ["API_TOKEN"]


def get_order(order_id):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    return cursor.fetchall()


def fetch_shipping_rate(url):
    response = requests.get(url, timeout=10)
    return response.json()["rate"]


def apply_tax(price, tax_rate):
    tax = price * tax_rate / 100
    return price + tax
