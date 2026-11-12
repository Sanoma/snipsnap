import sys

__version__ = "1"
__all__ = ["EXPORTED"]
USED = 1
EXPORTED = 2
UNUSED = 3
ANNOTATED: int = 4
BARE_ANNOTATION: int
CHAINED_A = CHAINED_B = 5
a, b = 1, 2
_ = sys.argv
ONLY_FOR_DEAD = 6
_private_unused = 7
counter = 0
counter += 1
MUTATED = []
MUTATED.append(USED)

if sys.platform == "win32":
    IN_BRANCH = 8
else:
    IN_BRANCH = 9

try:
    import json
except ImportError:
    json = None
    FALLBACK = 10


def dead_func():
    LOCAL = 1
    return ONLY_FOR_DEAD


def main():
    return json
