# Golden fixture: constructor parameter never stored on self, but read
# elsewhere in the class.
# Expected: analyzers.unstored_constructor_param_checker fires.
# (The exact bug found by manual review earlier this session.)


class Invoice:
    def __init__(self, customer, total):
        self.customer = customer

    def summary(self):
        return f"{self.customer}: {self.total}"
