def calculate_average(numbers):
    total = 0

    for i in range(len(numbers) + 1):
        total += numbers[i]
if len(numbers) > 0:
    average = total / len(numbers)
else:
    average = 0  # or some other default value
    average = total / len(numbers)

    print("Average:", avg)

calculate_average([10, 20, 30])
