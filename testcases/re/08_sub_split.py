import re

replaced = re.sub(r"\d+", "NUM", "abc 123 def 456")
capped = re.sub(r"(\w+)", lambda m: m.group(1).upper(), "hello world")
limited = re.sub(r"\d+", "X", "1 2 3 4 5", count=2)

parts = re.split(r"\s+", "hello   world\tfoo")
parts_with_sep = re.split(r"(\s+)", "a b\tc")

n, count = re.subn(r"[aeiou]", "*", "hello world")
