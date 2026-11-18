"""A judge model that is never used: ToolCorrectnessMetric builds an LLM in its
constructor (an OpenAI one by default, which needs OPENAI_API_KEY) even when, with
no `available_tools`, it scores deterministically and never calls it. DeepEval's
documented way to choose the judge is a custom `DeepEvalBaseLLM` passed as
`model=` (https://deepeval.com/guides/guides-using-custom-llms). This one raises
if called, so a hosted LLM is never reached and any call would show as a failure.
"""

from __future__ import annotations

from deepeval.models import DeepEvalBaseLLM


class NoLLM(DeepEvalBaseLLM):
    def __init__(self) -> None:
        super().__init__(model="no-llm")

    def load_model(self) -> "NoLLM":
        return self

    def generate(self, *args: object, **kwargs: object) -> str:
        raise RuntimeError("no LLM in this experiment: a deterministic metric asked for one")

    async def a_generate(self, *args: object, **kwargs: object) -> str:
        return self.generate()

    def get_model_name(self) -> str:
        return "no-llm"
