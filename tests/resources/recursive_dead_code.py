def used(n):
    return 1 if n == 0 else n * used(n - 1)


def dead_self(n):
    return 0 if n == 0 else dead_self(n - 1)


def ping(n):
    return pong(n - 1) if n else 0


def pong(n):
    return ping(n - 1) if n else 0


def dead_caller():
    return helper()


def helper():
    return 1


def main():
    return used(3)
