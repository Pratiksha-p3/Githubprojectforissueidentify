# Golden fixture: a name used with no binding anywhere in scope.
# Expected: analyzers.undefined_name_checker fires.


def process_order(order):
    total = 0
    for item in order["items"]:
        total += item["price"]

    return amount
