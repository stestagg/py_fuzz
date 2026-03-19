try:
    result = 1 / 0
except ZeroDivisionError as e:
    result = -1
except (TypeError, ValueError):
    result = -2
else:
    result = 0
finally:
    pass

class CustomError(Exception):
    pass

try:
    raise CustomError("oops")
except CustomError:
    pass
