# Adversarial variant of constructor_param_bug.py: multiple missing
# params, an extra correctly-stored param mixed in, and the read happens
# in a third method rather than immediately after __init__, to confirm
# the checker still connects "stored nowhere" to "read somewhere in the
# class" across more indirection than the minimal fixture.
# Expected: analyzers.unstored_constructor_param_checker still fires,
# for both `currency` and `tax_rate`.


class LineItem:
    def __init__(self, description, amount, currency, tax_rate):
        self.description = description
        self.amount = amount

    def label(self):
        return self.description

    def total_with_tax(self):
        return self.amount * (1 + self.tax_rate)

    def formatted(self):
        return f"{self.amount} {self.currency}"
