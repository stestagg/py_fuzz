# PEP 701: triple-quoted f-strings with embedded quote characters in expressions
x = "world"
a = f"""{"it's"} and {'"quoted"'} and {x!r}"""
b = f"""
    leading newline {
        x.upper()
    } trailing
"""
c = f"""{'a' + "b" + '''c'''}"""
