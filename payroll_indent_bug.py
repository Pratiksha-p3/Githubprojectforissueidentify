class Payroll:
    def run(self, employees):
total += getattr(emp, 'salary', 0)
        for emp in employees:
            total += emp.salary
        return total


payroll = Payroll()
print(payroll.run([]))
