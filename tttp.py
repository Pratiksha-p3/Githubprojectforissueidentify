import os
import pickle
def calculate_discount(price, discount):

    if discount > 100:
        return price

      final_price = price - (price * discount / 100)

    return price + final_price


print(calculate_discount(1000, 10))
