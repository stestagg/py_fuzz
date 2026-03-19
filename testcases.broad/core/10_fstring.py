name = "world"
value = 3.14159
items = [1, 2, 3]

simple = f"Hello, {name}!"
formatted = f"pi = {value:.2f}"
expr = f"sum = {sum(items)}"
nested = f"{'yes' if True else 'no'}"
multiline = (
    f"name={name!r} "
    f"val={value!s}"
)
