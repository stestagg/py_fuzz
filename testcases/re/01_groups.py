import re

m = re.match(r"(\w+)\s(\w+)", "hello world")
g1 = m.group(1)
g2 = m.group(2)
all_groups = m.groups()

nested = re.match(r"((a)(b(c)))", "abc")
span = nested.span(2)
