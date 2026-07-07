import io
d = io.IncrementalNewlineDecoder.__new__(io.IncrementalNewlineDecoder)
d.decode(b"line\r\n")
d.newlines
d.getstate()
