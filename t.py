def divide(a, b):
    if b == 0:
        raise ValueError("Division by zero")
    return a / b

items = [10, 20, 30]
index = 5

if index >= len(items):
    raise IndexError("Index out of range")
value = items[index]


result = divide(10, 0)

name = username

print("Result:", result)
print(index)
