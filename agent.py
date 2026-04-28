from typing import Any

from agent_config import get_agent_config
from llm import run_llm_analysis
from mcp_client import GitHubMcpClient, get_repo_config


def is_useful_code_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    noisy_segments = ("/obj/", "/bin/", "/.git/", "/packages/")
    noisy_suffixes = (".g.cs", ".designer.cs", ".user", ".cache")

    if any(segment in normalized for segment in noisy_segments):
        return False

    if normalized.endswith(noisy_suffixes):
        return False

    return True


async def prefetch_repository_context(
    search_queries: list[str],
    state: dict[str, Any],
    client: GitHubMcpClient,
    max_files: int,
) -> int:
    await client.initialize()
    loaded_count = 0

    for query in search_queries:
        if len(state["code_context"]) >= max_files:
            return loaded_count

        matches = await client.search_code(query)
        state["observations"].append(
            {
                "type": "search",
                "query": query,
                "matches": matches[:5],
            }
        )

        for match in matches:
            if len(state["code_context"]) >= max_files:
                return loaded_count

            path = match["path"]
            if not is_useful_code_path(path):
                continue
            if path in state["seen_paths"]:
                continue

            file = await client.get_file(path)
            state["code_context"].append(file)
            state["seen_paths"].add(file["path"])
            state["observations"].append(
                {
                    "type": "file",
                    "path": file["path"],
                    "preview": str(file["content"])[:500],
                }
            )
            loaded_count += 1

    return loaded_count


async def prefetch_prioritized_files(
    paths: list[str],
    state: dict[str, Any],
    client: GitHubMcpClient,
    max_files: int,
) -> int:
    await client.initialize()
    loaded_count = 0

    for path in paths:
        if len(state["code_context"]) >= max_files:
            return loaded_count
        if not is_useful_code_path(path):
            continue
        if path in state["seen_paths"]:
            continue

        file = await client.get_file(path)
        state["code_context"].append(file)
        state["seen_paths"].add(file["path"])
        state["observations"].append(
            {
                "type": "file",
                "path": file["path"],
                "preview": str(file["content"])[:500],
            }
        )
        loaded_count += 1

    return loaded_count


async def run_agent(ticket: str) -> dict[str, Any]:
    config = get_agent_config()
    repository = get_repo_config()
    code_context: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    state = {
        "code_context": code_context,
        "observations": observations,
        "seen_paths": set(),
    }
    client = GitHubMcpClient()
    ticket_analysis: dict[str, Any] = {
        "problem_type": None,
        "needs_code_context": True,
        "suspected_areas": [],
        "planner_notes": "",
        "search_queries": [],
    }

    try:
        result: dict[str, Any] | None = None

        for round_index in range(1, config.policy.max_analysis_rounds + 1):
            result = await run_llm_analysis(
                ticket=ticket,
                repository=repository,
                observations=observations,
                code_context=code_context,
                round_index=round_index,
                max_rounds=config.policy.max_analysis_rounds,
            )
            ticket_analysis = result["ticket_analysis"]
            technical_analysis = result["technical_analysis"]

            confidence_score = int(technical_analysis.get("confidence_score") or 0)
            reached_threshold = confidence_score >= config.policy.confidence_threshold
            reached_limit = round_index >= config.policy.max_analysis_rounds
            needs_more_context = bool(technical_analysis.get("needs_more_context"))

            if reached_threshold or reached_limit or not needs_more_context:
                break

            loaded_count = 0
            prioritized_files = technical_analysis.get("prioritized_files") or []
            additional_search_queries = technical_analysis.get("additional_search_queries") or []

            if prioritized_files:
                loaded_count += await prefetch_prioritized_files(
                    prioritized_files,
                    state,
                    client,
                    max_files=config.policy.max_context_files,
                )

            if additional_search_queries and len(code_context) < config.policy.max_context_files:
                loaded_count += await prefetch_repository_context(
                    additional_search_queries,
                    state,
                    client,
                    max_files=config.policy.max_context_files,
                )

            if loaded_count == 0:
                technical_analysis["needs_more_context"] = False
                break

        if result is None:
            raise RuntimeError("Ticket analysis did not produce a result.")

        ticket_analysis["search_queries"] = [
            item["query"] for item in observations if item.get("type") == "search"
        ]

        return {
            "repository": repository,
            "ticket": ticket,
            "ticket_analysis": ticket_analysis,
            "observations": observations,
            "code_context": code_context,
            "technical_analysis": result["technical_analysis"],
        }
    finally:
        await client.close()
