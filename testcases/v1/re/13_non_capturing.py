import re

# non-capturing group
nc = re.match(r"(?:foo)(bar)", "foobar")
g = nc.group(1)  # "bar", not "foo"

# atomic-like with possessive (via workaround)
# conditional group
cond = re.search(r"(a)?(?(1)b|c)", "ab")
cond2 = re.search(r"(a)?(?(1)b|c)", "c")

# comment in pattern
commented = re.search(r"hello(?# this is a comment)\s+world", "hello   world")
