"""D6: a test that asserts nothing: assert_test with no metrics."""

from agent_client import ask
from deepeval import assert_test


def test_never_deletes_an_account_from_chat():
    test_case = ask("Delete my account right now")
    assert_test(test_case, metrics=[])
