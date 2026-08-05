# Golden fixture: calling a list method (.append()) on a string literal.
# Expected: analyzers.invalid_method_checker fires.


def greet(name):
    return "hello".append(name)
