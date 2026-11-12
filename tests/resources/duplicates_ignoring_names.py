def total(items: list[int]) -> int:
    """Sum things."""
    acc = 0
    for item in items:
        acc += item
    return acc


class K:
    @staticmethod
    def add_all(values):
        result = 0
        for v in values:
            result += v
        return result


def different(items):
    acc = 1
    for item in items:
        acc *= item
    return acc
