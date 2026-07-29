def calculate_average(numbers):
    total = 0

    for i in range(len(numbers) + 1):
        total += numbers[i]

    average = total / len(numbers)

    print("Average:", avg)

calculate_average([10, 20, 30])
