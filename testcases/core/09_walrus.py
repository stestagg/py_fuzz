import re

data = [1, 2, 3, 4, 5, 6]
filtered = [y for x in data if (y := x * 2) > 4]

text = "hello world"
if m := re.search(r"(\w+)\s+(\w+)", text):
    first, second = m.group(1), m.group(2)

while chunk := data[:2]:
    data = data[2:]
