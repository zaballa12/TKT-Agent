from typing import Any

from llm import MAX_CONTEXT_FILES, plan_ticket_locally, run_llm_analysis
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
    max_files: int = MAX_CONTEXT_FILES,
) -> None:
    await client.initialize()

    for query in search_queries:
        matches = await client.search_code(query)
        state["observations"].append(
            {
                "type": "search",
                "query": query,
                "matches": matches[:5],
            }
        )
        state["mcp"]["attempted"] = True
        state["mcp"]["used"] = True
        state["mcp"]["searches"] += 1
        state["mcp"]["tool_observations"] += 1

        for match in matches:
            if len(state["code_context"]) >= max_files:
                return

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
            state["mcp"]["files_loaded"] += 1
            state["mcp"]["tool_observations"] += 1


async def run_agent(ticket: str) -> dict[str, Any]:
    repository = get_repo_config()
    mcp = {
        "attempted": False,
        "used": False,
        "searches": 0,
        "files_loaded": 0,
        "tool_observations": 0,
    }
    code_context: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    state = {
        "code_context": code_context,
        "observations": observations,
        "mcp": mcp,
        "seen_paths": set(),
    }
    client = GitHubMcpClient()
    ticket_analysis = plan_ticket_locally(ticket)

    try:
        if ticket_analysis["needs_code_context"] and ticket_analysis["search_queries"]:
            await prefetch_repository_context(ticket_analysis["search_queries"], state, client)

        result = await run_llm_analysis(
            ticket=ticket,
            repository=repository,
            execute_tool=None,
            fallback_context={
                "code_context": code_context,
                "observations": observations,
            },
            ticket_analysis=ticket_analysis,
        )
        ticket_analysis = result["ticket_analysis"]
        executed_queries = [
            item["query"] for item in observations if item.get("type") == "search"
        ]
        if executed_queries:
            ticket_analysis["search_queries"] = executed_queries

        return {
            "repository": repository,
            "ticket": ticket,
            "ticket_analysis": ticket_analysis,
            "mcp": mcp,
            "observations": observations,
            "code_context": code_context,
            "technical_analysis": result["technical_analysis"],
        }
    finally:
        await client.close()
