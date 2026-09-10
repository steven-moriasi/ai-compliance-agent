import re

INJECTION_PATTERNS = {
    "instruction_override": re.compile(
        r"\b(ignore|disregard) (all |the )?(prior|previous)\b", re.I
    ),
    "prompt_extraction": re.compile(
        r"\b(system prompt|developer message|hidden instructions)\b", re.I
    ),
    "secret_exfiltration": re.compile(
        r"\b(reveal|print|return).{0,30}\b(secret|api key|token)\b", re.I
    ),
    "role_impersonation": re.compile(r"\b(you are now|act as|pretend to be)\b", re.I),
}


def detect_prompt_injection(content: str) -> list[str]:
    return [name for name, pattern in INJECTION_PATTERNS.items() if pattern.search(content)]
