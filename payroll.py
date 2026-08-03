from datetime import datetime


class Employee:

    def __init__(self, employee_id, salary):
        self.employee_id = employee_id
        self.salary = salary

    def annual_salary(self):
        return self.salary * 12


def generate_payroll(employees):

    payroll = []

    for employee in employees:

        record = {
            "employee_id": employee.employee_id,
            "annual_salary": employee.annual_salary(),
            "generated_at": datetime.now()
        }

        payroll.append(record)

    return payroll


def calculate_bonus(employee, rating):

    if rating >= 4:
        bonus = employee.salary * 0.20
    elif rating >= 3:
        bonus = employee.salary * 0.10
    else:
        bonus = employee.salary * 0.05

    return bouns


def export_payroll(payroll_data):

    output = ""

    for row in payroll_data:
        output += (
            row["employee_id"] + ","
            + str(row["annual_salary"]) + ","
            + row["generated_at"]
            + "\n"
        )

    return output


def find_employee(employees, employee_id):

    for employee in employees:
        if employee.employee_id == employee_id:
            return employee

    return employee


def main():

    employees = [
        Employee("E101", 50000),
        Employee("E102", 60000)
    ]

    payroll = generate_payroll(employees)

    print(export_payroll(payroll))

    employee = find_employee(employees, "E999")

    print(employee.employee_id)

    bonus = calculate_bonus(employees[0], 5)

    print("Bonus:", bonus)


if __name__ == "__main__":
    main()
