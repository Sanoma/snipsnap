import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pkg.b import Fwd


def used_func(x: "Fwd"):
    return sys.argv


def dead_func():
    return 1


class DeadClass:
    def method(self):
        pass


class Base:
    def hook(self):
        pass


class Child(Base):
    def other_hook(self):
        pass


def cli():
    pass


@some_decorator
def decorated():
    pass


def __getattr__(name):
    pass
