def ping(n):
    return pong(n - 1) if n else 0


def pong(n):
    return ping(n - 1) if n else 0


def main():
    return 1
