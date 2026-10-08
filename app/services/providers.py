import json
from dataclasses import dataclass
from datetime import date
from time import monotonic
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.domain.enums import AnalysisOutcome
from app.domain.schemas import AnswerCitation, Citation, ModelAnalysis, ModelAnswer
from app.services.retrieval import RetrievedPolicy


@dataclass(frozen=True)
class ModelRequest:
    system_prompt: str
    document: str
    policies: list[RetrievedPolicy]
    validation_feedback: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelResponse:
    analysis: ModelAnalysis
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass(frozen=True)
class AnswerRequest:
    system_prompt: str
    question: str
    as_of: date
    sources: list[RetrievedPolicy]
    validation_feedback: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerResponse:
    answer: ModelAnswer
    input_tokens: int
    output_tokens: int
    latency_ms: int


class ModelProvider(Protocol):
    name: str
    model: str

    def analyze(self, request: ModelRequest) -> ModelResponse: ...


class AnswerProvider(Protocol):
    name: str
    model: str

    def answer(self, request: AnswerRequest) -> AnswerResponse: ...


class LanguageModel(ModelProvider, AnswerProvider, Protocol):
    """A provider the worker can use for both case analysis and policy questions."""


class MalformedModelOutputError(ValueError):
    pass


class DeterministicProvider:
    name = "deterministic"
    model = "local-reference-1"

    def analyze(self, request: ModelRequest) -> ModelResponse:
        started = monotonic()
        if not request.policies:
            raise ValueError("No relevant active policy was retrieved")
        policy = request.policies[0]
        quote = policy.content[: min(len(policy.content), 240)]
        analysis = ModelAnalysis(
            outcome=AnalysisOutcome.NEEDS_REVIEW,
            confidence=0.65,
            rationale=(
                "A human must confirm how the cited policy applies to the submitted document."
            ),
            citations=[
                Citation(
                    policy_id=policy.id,
                    policy_version=policy.version,
                    section_ref=policy.section_ref,
                    quote=quote,
                )
            ],
        )
        return ModelResponse(
            analysis=analysis,
            input_tokens=len(request.document.split()) + len(policy.content.split()),
            output_tokens=len(analysis.rationale.split()) + len(quote.split()),
            latency_ms=max(round((monotonic() - started) * 1000), 1),
        )

    def answer(self, request: AnswerRequest) -> AnswerResponse:
        """Quote the opening of the first source. It exercises the pipeline, not reasoning."""
        started = monotonic()
        if not request.sources:
            answer = ModelAnswer(
                supported=False,
                answer="The supplied sources do not address this question.",
                citations=[],
            )
        else:
            source = request.sources[0]
            quote = _leading_passage(source.content)
            answer = ModelAnswer(
                supported=True,
                answer=f"Source 1 ({source.name}, {source.section_ref}) states: {quote}",
                citations=[AnswerCitation(source=1, quote=quote)],
            )
        context_words = sum(len(source.content.split()) for source in request.sources)
        return AnswerResponse(
            answer=answer,
            input_tokens=len(request.question.split()) + context_words,
            output_tokens=len(answer.answer.split()),
            latency_ms=max(round((monotonic() - started) * 1000), 1),
        )


class OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str


class OpenAIChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: OpenAIMessage


class OpenAIUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int
    completion_tokens: int


class OpenAICompletion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    choices: list[OpenAIChoice]
    usage: OpenAIUsage


class OpenAICompatibleProvider:
    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
        provider_name: str = "openai-compatible",
        json_mode: Literal["json_schema", "json_object"] = "json_schema",
        timeout_seconds: float = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = client
        self.name = provider_name
        self.json_mode = json_mode
        self.timeout_seconds = timeout_seconds

    def analyze(self, request: ModelRequest) -> ModelResponse:
        started = monotonic()
        policy_context = [
            {
                "policy_id": policy.id,
                "name": policy.name,
                "version": policy.version,
                "section_ref": policy.section_ref,
                "heading": policy.heading,
                "content": policy.content,
            }
            for policy in request.policies
        ]
        user_request: dict[str, object] = {
            "instruction": "Analyze only against the supplied policy context.",
            "untrusted_document": request.document,
            "policy_context": policy_context,
        }
        if request.validation_feedback:
            user_request["instruction"] = (
                "Revise the prior analysis against the supplied policy context and correct "
                "every validation feedback item."
            )
            user_request["validation_feedback"] = list(request.validation_feedback)
        content, usage = self._chat(
            request.system_prompt,
            user_request,
            schema_name="compliance_analysis",
            schema=ModelAnalysis.model_json_schema(),
        )
        try:
            analysis = ModelAnalysis.model_validate_json(_json_object_text(content))
        except ValidationError as error:
            raise MalformedModelOutputError("Model output did not match the schema") from error
        return ModelResponse(
            analysis=analysis,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            latency_ms=max(round((monotonic() - started) * 1000), 1),
        )

    def answer(self, request: AnswerRequest) -> AnswerResponse:
        started = monotonic()
        user_request: dict[str, object] = {
            "instruction": (
                "Answer the question from the numbered sources only. Cite each statement with "
                "the source number and a quote copied exactly from that source."
            ),
            "untrusted_question": request.question,
            "as_of": request.as_of.isoformat(),
            "sources": [
                {
                    "source": number,
                    "policy": source.name,
                    "version": source.version,
                    "section": source.section_ref,
                    "heading": source.heading,
                    "text": source.content,
                }
                for number, source in enumerate(request.sources, start=1)
            ],
        }
        if request.validation_feedback:
            user_request["instruction"] = (
                "Your previous answer failed these checks. Answer again from the numbered "
                "sources and fix every item."
            )
            user_request["validation_feedback"] = list(request.validation_feedback)
        content, usage = self._chat(
            request.system_prompt,
            user_request,
            schema_name="policy_answer",
            schema=ModelAnswer.model_json_schema(),
        )
        try:
            answer = ModelAnswer.model_validate_json(_json_object_text(content))
        except ValidationError as error:
            raise MalformedModelOutputError("Model output did not match the schema") from error
        return AnswerResponse(
            answer=answer,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            latency_ms=max(round((monotonic() - started) * 1000), 1),
        )

    def _chat(
        self,
        system_prompt: str,
        user_request: dict[str, object],
        *,
        schema_name: str,
        schema: dict[str, object],
    ) -> tuple[str, OpenAIUsage]:
        if self.json_mode == "json_object":
            system_prompt = (
                f"{system_prompt}\n\nRespond with one JSON object matching this schema:\n"
                f"{json.dumps(schema)}"
            )
            response_format: dict[str, object] = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_request)},
            ],
            "response_format": response_format,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.client is None:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=payload
                )
        else:
            response = self.client.post(
                f"{self.base_url}/chat/completions", headers=headers, json=payload
            )
        response.raise_for_status()
        completion = OpenAICompletion.model_validate(response.json())
        if not completion.choices:
            raise MalformedModelOutputError("Model provider returned no choices")
        return completion.choices[0].message.content, completion.usage


def _json_object_text(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _leading_passage(text: str, limit: int = 240) -> str:
    """The first `limit` characters, cut back to a whole word so the quote still matches."""
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rsplit(" ", 1)[0]
