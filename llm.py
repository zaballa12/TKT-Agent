from functools import lru_cache
import asyncio
import json
from typing import Any

from runtime import ensure_local_packages

ensure_local_packages()

from google import genai
from google.genai import types

from agent_config import AgentConfig, get_agent_config
from settings import get_settings


@lru_cache(maxsize=1)
def get_gemini_client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required to analyze tickets.")
    return genai.Client(api_key=settings.gemini_api_key)


def format_observation_summary(observations: list[dict[str, Any]]) -> str:
    if not observations:
        return "No repository observations collected yet."

    lines: list[str] = []
    for item in observations:
        item_type = item.get("type")
        if item_type == "search":
            query = item.get("query") or ""
            matches = item.get("matches") or []
            top_paths = [match.get("path") for match in matches[:3] if match.get("path")]
            lines.append(
                f'SEARCH query="{query}" matches={len(matches)}'
                + (f" top_paths={', '.join(top_paths)}" if top_paths else "")
            )
            continue

        if item_type == "file":
            path = item.get("path") or ""
            preview = str(item.get("preview") or "")[:260]
            lines.append(f'FILE path="{path}" excerpt="{preview}"')
            continue

        lines.append(json.dumps(item, ensure_ascii=False))

    return "\n".join(lines)


def format_file_context(code_context: list[dict[str, Any]], config: AgentConfig) -> str:
    if not code_context:
        return "No repository files were loaded."

    chunks: list[str] = []
    for file in code_context[: config.policy.max_context_files]:
        content = str(file.get("content") or "")[: config.policy.max_file_chars]
        chunks.append(f"FILE: {file['path']}\n```text\n{content}\n```")
    return "\n\n".join(chunks)


def format_option_lines(options: tuple[Any, ...]) -> str:
    return "\n".join(f"- {option.id}: {option.description}" for option in options)


def build_analysis_prompt(
    ticket: str,
    repository: dict[str, Any],
    observations: list[dict[str, Any]],
    code_context: list[dict[str, Any]],
    round_index: int,
    max_rounds: int,
    config: AgentConfig,
) -> str:
    repository_name = f"{repository['owner']}/{repository['repo']}"
    if repository.get("ref"):
        repository_name = f"{repository_name} at ref {repository['ref']}"

    return config.analysis_prompt_template.format(
        repository_name=repository_name,
        round_index=round_index,
        max_rounds=max_rounds,
        confidence_threshold=config.policy.confidence_threshold,
        ticket_categories=format_option_lines(config.ticket_categories),
        recommended_actions=format_option_lines(config.recommended_actions),
        confidence_levels=", ".join(config.confidence_levels),
        ticket=ticket,
        observation_summary=format_observation_summary(observations),
        file_context=format_file_context(code_context, config),
    )


def build_response_schema(config: AgentConfig) -> dict[str, Any]:
    category_ids = [option.id for option in config.ticket_categories]
    action_ids = [option.id for option in config.recommended_actions]

    return {
        "type": "object",
        "properties": {
            "problem_type": {"type": "string", "enum": category_ids},
            "needs_code_context": {"type": "boolean"},
            "suspected_areas": {"type": "array", "items": {"type": "string"}},
            "planner_notes": {"type": "string"},
            "analysis": {"type": "string"},
            "suggested_reply": {"type": "string"},
            "dev_activity": {"type": "string"},
            "possible_changes": {"type": "array", "items": {"type": "string"}},
            "next_steps": {"type": "array", "items": {"type": "string"}},
            "files_to_check": {"type": "array", "items": {"type": "string"}},
            "evidence_files": {"type": "array", "items": {"type": "string"}},
            "repo_evidence_found": {"type": "boolean"},
            "confidence": {"type": "string", "enum": list(config.confidence_levels)},
            "confidence_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
            "needs_more_context": {"type": "boolean"},
            "additional_search_queries": {"type": "array", "items": {"type": "string"}},
            "prioritized_files": {"type": "array", "items": {"type": "string"}},
            "recommended_action": {"type": "string", "enum": action_ids},
        },
        "required": [
            "problem_type",
            "needs_code_context",
            "suspected_areas",
            "planner_notes",
            "analysis",
            "suggested_reply",
            "dev_activity",
            "possible_changes",
            "next_steps",
            "files_to_check",
            "evidence_files",
            "repo_evidence_found",
            "confidence",
            "confidence_score",
            "complexity",
            "needs_more_context",
            "additional_search_queries",
            "prioritized_files",
            "recommended_action",
        ],
    }


def parse_model_payload(response_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Model returned invalid JSON: {response_text}") from error

    if not isinstance(payload, dict):
        raise RuntimeError(f"Model returned unexpected JSON payload: {response_text}")

    return payload


async def run_llm_analysis(
    ticket: str,
    repository: dict[str, Any],
    observations: list[dict[str, Any]],
    code_context: list[dict[str, Any]],
    round_index: int,
    max_rounds: int,
) -> dict[str, Any]:
    config = get_agent_config()
    settings = get_settings()
    prompt = build_analysis_prompt(
        ticket=ticket,
        repository=repository,
        observations=observations,
        code_context=code_context,
        round_index=round_index,
        max_rounds=max_rounds,
        config=config,
    )

    def call_model() -> types.GenerateContentResponse:
        return get_gemini_client().models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=config.system_instruction,
                responseMimeType="application/json",
                responseJsonSchema=build_response_schema(config),
            ),
        )

    try:
        response = await asyncio.to_thread(call_model)
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"Gemini SDK error: {error}") from error

    payload = parse_model_payload(response.text or "")

    return {
        "ticket_analysis": {
            "problem_type": payload.get("problem_type"),
            "needs_code_context": bool(payload.get("needs_code_context")),
            "suspected_areas": payload.get("suspected_areas") or [],
            "planner_notes": payload.get("planner_notes") or "",
        },
        "technical_analysis": {
            "analysis": payload.get("analysis") or "",
            "suggested_reply": payload.get("suggested_reply") or "",
            "dev_activity": payload.get("dev_activity") or "",
            "possible_changes": payload.get("possible_changes") or [],
            "next_steps": payload.get("next_steps") or [],
            "files_to_check": payload.get("files_to_check") or [],
            "evidence_files": payload.get("evidence_files") or [],
            "repo_evidence_found": bool(payload.get("repo_evidence_found")),
            "confidence": payload.get("confidence") or config.confidence_levels[0],
            "confidence_score": int(payload.get("confidence_score") or 0),
            "complexity": payload.get("complexity") or "low",
            "needs_more_context": bool(payload.get("needs_more_context")),
            "additional_search_queries": payload.get("additional_search_queries") or [],
            "prioritized_files": payload.get("prioritized_files") or [],
            "recommended_action": payload.get("recommended_action") or config.recommended_actions[0].id,
            "observation_summary": format_observation_summary(observations),
        },
    }
