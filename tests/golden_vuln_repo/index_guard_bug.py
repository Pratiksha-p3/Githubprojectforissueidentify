# Golden fixture: unguarded list index access.
# Expected: analyzers.index_guard_checker fires.


def first_item(items):
    return items[0]
