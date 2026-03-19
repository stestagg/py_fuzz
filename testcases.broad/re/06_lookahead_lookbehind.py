import re

# positive lookahead
pos_ahead = re.findall(r"\w+(?=\s+world)", "hello world foo")

# negative lookahead
neg_ahead = re.findall(r"\w+(?!\s+world)", "hello world foo")

# positive lookbehind
pos_behind = re.findall(r"(?<=hello\s)\w+", "hello world")

# negative lookbehind
neg_behind = re.findall(r"(?<!hello\s)\w+", "hello world bye")
