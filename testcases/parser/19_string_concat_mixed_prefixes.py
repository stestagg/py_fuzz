# Implicit string concatenation with mixed prefixes (r, b, u, rb, br)
a = "hello" " " "world"
b = r"raw\n" "normal" r"\t" "end"
c = (
    "line one "
    "line two "
    r"line\three "
    "line four"
)
d = b"bytes" b"\x00\xff" b"more"
e = rb"raw" rb"\x00" br"bytes"
f = u"unicode" "plain" u"more"
g = ("multi"
     "line"
     "concat"
     r"raw\"here")
