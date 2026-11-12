from lib import Tested, only_tests, recursive


def test_all():
    assert only_tests() and recursive(2) is not None and Tested().run()
