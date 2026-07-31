# Golden fixture: division by an unguarded parameter.
# Expected: analyzers.division_guard_checker fires on line 3.


def average_per_item(total, count):
    return total / count
