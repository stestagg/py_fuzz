import re

digits = re.findall(r"\d+", "abc 123 def 456")
words = re.findall(r"\w+", "hello_world 42")
spaces = re.findall(r"\s+", "a b\tc\nd")
nondigits = re.findall(r"\D+", "abc123def")

custom = re.findall(r"[aeiou]", "hello world")
negated = re.findall(r"[^aeiou\s]+", "hello world")
range_cls = re.findall(r"[a-fA-F0-9]+", "deadBEEF 99")
