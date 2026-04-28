from dataclasses import dataclass
from functools import lru_cache
import json

from settings import get_settings


@dataclass(frozen=True)
class AgentOption:
    id: str
    label: str
    description: str


@dataclass(frozen=True)
class AgentPolicy:
    confidence_threshold: int
    max_analysis_rounds: int
    max_context_files: int
    max_file_chars: int


@dataclass(frozen=True)
class AgentConfig:
    ticket_categories: tuple[AgentOption, ...]
    recommended_actions: tuple[AgentOption, ...]
    confidence_levels: tuple[str, ...]
    policy: AgentPolicy
    system_instruction: str
    analysis_prompt_template: str


@lru_cache(maxsize=1)
def get_agent_config() -> AgentConfig:
    settings = get_settings()
    options_payload = json.loads((settings.agent_config_dir / "options.json").read_text(encoding="utf-8"))

    policy_payload = options_payload["policy"]
    policy = AgentPolicy(
        confidence_threshold=settings.confidence_threshold_override
        or int(policy_payload["confidence_threshold"]),
        max_analysis_rounds=settings.max_analysis_rounds_override
        or int(policy_payload["max_analysis_rounds"]),
        max_context_files=settings.max_context_files_override or int(policy_payload["max_context_files"]),
        max_file_chars=settings.max_file_chars_override or int(policy_payload["max_file_chars"]),
    )

    return AgentConfig(
        ticket_categories=_load_options(options_payload["ticket_categories"]),
        recommended_actions=_load_options(options_payload["recommended_actions"]),
        confidence_levels=tuple(options_payload["confidence_levels"]),
        policy=policy,
        system_instruction=(settings.agent_config_dir / "system_instruction.md").read_text(encoding="utf-8").strip(),
        analysis_prompt_template=(settings.agent_config_dir / "analysis_prompt.md").read_text(encoding="utf-8").strip(),
    )


def _load_options(payload: list[dict[str, str]]) -> tuple[AgentOption, ...]:
    return tuple(
        AgentOption(
            id=item["id"],
            label=item["label"],
            description=item["description"],
        )
        for item in payload
    )
