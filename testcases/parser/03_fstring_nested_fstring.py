# PEP 701: f-string containing f-string in expression — recursive PEG f-string rule
x = 7
a = f"{f'{x}'}"
b = f"{f"{f'{x + 1}'}"}"
c = f"outer {f'mid {f"inner {x}"}'} end"
d = f"{f'{x!r}'!s:>10}"
