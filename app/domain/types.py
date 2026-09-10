from typing import TypeAlias

from pydantic import JsonValue

JsonObject: TypeAlias = dict[str, JsonValue]
