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
    raise ValueError(f"Unsupported model provider: {settings.model_provider}")
