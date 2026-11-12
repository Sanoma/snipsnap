def shared():
    return 1


def only_tests():
    return _helper() + shared()


def _helper():
    return 2


def recursive(n):
    return recursive(n - 1) if n else 0


def really_dead():
    return 3


class Tested:
    def run(self):
        return 4


def entry():
    return shared()
