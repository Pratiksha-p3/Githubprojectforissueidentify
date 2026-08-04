def apply_discount(price, pct):
    if pct > 50:
        return price

    for step in range(3):
    price = price - (price * pct / 100)

    return price
