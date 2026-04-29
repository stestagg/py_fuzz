# PEP 614: relaxed decorator grammar — arbitrary primary expressions
import module

@module.registry["key"].decorator(arg1, arg2)
def f(): ...

@(lambda fn: fn)
def g(): ...

@module.get_decorator()(extra)
class C: ...

@decorators[0]
@decorators[1](param=True)
@obj.method(a, b).result
def h(): ...
