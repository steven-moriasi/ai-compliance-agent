from app.core.config import Settings
from app.services.providers import DeterministicProvider, ModelProvider, OpenAICompatibleProvider


def build_provider(settings: Settings) -> ModelProvider:
    if settings.model_provider == "deterministic":
        return DeterministicProvider()
    if settings.model_provider == "openai-compatible":
        if settings.model_api_key is None:
            raise ValueError("A model API key is required for the configured provider")
        return OpenAICompatibleProvider(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key.get_secret_value(),
            model=settings.model_name,
        )
    if settings.model_provider == "ollama":
        api_key = "ollama"
        if settings.model_api_key is not None:
            api_key = settings.model_api_key.get_secret_value()
        return OpenAICompatibleProvider(
            base_url=settings.local_model_base_url,
            api_key=api_key,
            model=settings.local_model_name,
            provider_name="ollama",
            json_mode="json_object",
            timeout_seconds=settings.local_model_timeout_seconds,
        )
    raise ValueError(f"Unsupported model provider: {settings.model_provider}")
