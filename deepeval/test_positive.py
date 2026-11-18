"""D8: the agent must call get_order and the reply must contain "in transit"."""

from agent_client import ask
from no_llm import NoLLM
from deepeval import assert_test
from deepeval.metrics import PatternMatchMetric, ToolCorrectnessMetric
from deepeval.test_case import ToolCall


def test_looks_the_order_up():
    test_case = ask("Where is order 1042?", expected_tools=[ToolCall(name="get_order")])
    assert_test(
        test_case,
        [ToolCorrectnessMetric(model=NoLLM()), PatternMatchMetric(pattern=r".*in transit.*")],
    )
