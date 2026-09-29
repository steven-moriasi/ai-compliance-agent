import json
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.domain.enums import AnalysisOutcome
from app.domain.schemas import Citation, ModelAnalysis
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


class ModelProvider(Protocol):
    name: str
    model: str

    def analyze(self, request: ModelRequest) -> ModelResponse: ...


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
            citations=[Citation(policy_id=policy.id, policy_version=policy.version, quote=quote)],
        )
        return ModelResponse(
            analysis=analysis,
            input_tokens=len(request.document.split()) + len(policy.content.split()),
            output_tokens=len(analysis.rationale.split()) + len(quote.split()),
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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = client

    def analyze(self, request: ModelRequest) -> ModelResponse:
        started = monotonic()
        policy_context = [
            {
                "policy_id": policy.id,
                "name": policy.name,
                "version": policy.version,
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
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_request),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "compliance_analysis",
                    "strict": True,
                    "schema": ModelAnalysis.model_json_schema(),
                },
            },
        }
        if self.client is None:
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        else:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        response.raise_for_status()
        completion = OpenAICompletion.model_validate(response.json())
        if not completion.choices:
            raise MalformedModelOutputError("Model provider returned no choices")
        try:
            analysis = ModelAnalysis.model_validate_json(completion.choices[0].message.content)
        except ValidationError as error:
            raise MalformedModelOutputError("Model output did not match the schema") from error
        return ModelResponse(
            analysis=analysis,
            input_tokens=completion.usage.prompt_tokens,
            output_tokens=completion.usage.completion_tokens,
            latency_ms=max(round((monotonic() - started) * 1000), 1),
        )
