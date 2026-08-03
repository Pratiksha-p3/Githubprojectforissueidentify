import requests
import sqlite3


API_TOKEN = "sk-live-abc123xyz"


def get_order(order_id):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = " + order_id)
    return cursor.fetchall()


def fetch_shipping_rate(url):
    response = requests.get(url)
    return response.json()["rate"]


def apply_tax(price, tax_rate):
    tax = price * tax_rate / 100
    return price + tax_rate
