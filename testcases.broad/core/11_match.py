def classify(point):
    match point:
        case (0, 0):
            return "origin"
        case (x, 0):
            return f"x-axis at {x}"
        case (0, y):
            return f"y-axis at {y}"
        case (x, y):
            return f"point at {x},{y}"

def http_error(status):
    match status:
        case 400:
            return "Bad request"
        case 404 | 405:
            return "Not allowed"
        case _:
            return "Other"
