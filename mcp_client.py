import bootstrap  # noqa: F401

import base64
import json
import os
import re
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MissingEnvironmentError(RuntimeError):
    pass


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise MissingEnvironmentError(f"Missing required environment variable: {name}")
    return value


def require_github_token() -> str:
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise MissingEnvironmentError(
            "Missing required environment variable: GITHUB_PERSONAL_ACCESS_TOKEN or GITHUB_TOKEN"
        )
    return token


def get_repo_config() -> dict[str, str | None]:
    return {
        "owner": require_env("GITHUB_OWNER"),
        "repo": require_env("GITHUB_REPO"),
        "ref": os.getenv("GITHUB_REF") or None,
    }


def get_default_docker_args() -> list[str]:
    args = ["run", "-i"]

    if os.getenv("MCP_DOCKER_CONTAINER_NAME"):
        args.extend(["--name", os.environ["MCP_DOCKER_CONTAINER_NAME"]])

    if os.getenv("MCP_DOCKER_KEEP_CONTAINER") != "true":
        args.append("--rm")

    if os.getenv("MCP_DOCKER_VOLUME"):
        args.extend(["-v", os.environ["MCP_DOCKER_VOLUME"]])

    args.extend(
        [
            "-e",
            "GITHUB_PERSONAL_ACCESS_TOKEN",
            "-e",
            "GITHUB_TOOLSETS",
            "ghcr.io/github/github-mcp-server",
            "stdio",
            "--read-only",
            "--toolsets=repos",
        ]
    )

    if os.getenv("MCP_ENABLE_COMMAND_LOGGING") == "true":
        log_file = os.getenv("MCP_LOG_FILE") or "/logs/github-mcp.log"
        args.extend(["--enable-command-logging", "--log-file", log_file])

    return args


def get_server_command_config() -> tuple[str, list[str]]:
    command = os.getenv("MCP_SERVER_COMMAND") or "docker"
    raw_args = os.getenv("MCP_SERVER_ARGS")
    if raw_args:
        args = [part.strip() for part in raw_args.split(",") if part.strip()]
    else:
        args = get_default_docker_args()
    return command, args


def get_server_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["GITHUB_PERSONAL_ACCESS_TOKEN"] = require_github_token()
    env["GITHUB_TOOLSETS"] = os.getenv("GITHUB_TOOLSETS") or "repos"
    return env


def extract_text_content(result: Any) -> str:
    content = getattr(result, "content", None) or []
    values = []

    for item in content:
        item_type = getattr(item, "type", None)
        text = getattr(item, "text", None)
        if item_type == "text" and isinstance(text, str):
            values.append(text)

    return "\n".join(values)


def extract_embedded_resource_text(result: Any) -> tuple[str, str | None]:
    content = getattr(result, "content", None) or []

    for item in content:
        resource = getattr(item, "resource", None)
        text = getattr(resource, "text", None)
        uri = getattr(resource, "uri", None)
        if isinstance(text, str) and text:
            text = text.lstrip("\ufeff")
            sha = None
            if uri:
                match = re.search(r"/sha/([0-9a-f]{40})/", str(uri))
                if match:
                    sha = match.group(1)
            return text, sha

    return "", None


def extract_status_sha(raw_text: str) -> str | None:
    match = re.search(r"SHA:\s*([0-9a-f]{40})", raw_text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def safe_json_parse(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def decode_maybe_base64(content: str | None, encoding: str | None) -> str:
    if not content:
        return ""
    if encoding != "base64":
        return content
    return base64.b64decode(content).decode("utf-8")


def normalize_search_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("results") or payload.get("items") or payload.get("matches") or []
    else:
        items = []

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue

        repository = item.get("repository")
        if isinstance(repository, dict):
            repository_name = repository.get("full_name")
        else:
            repository_name = repository

        path = item.get("path") or item.get("name") or ""
        if not path:
            continue

        text_matches = item.get("text_matches") or []
        first_match = text_matches[0] if text_matches and isinstance(text_matches[0], dict) else {}
        normalized.append(
            {
                "path": path,
                "repository": repository_name
                or f"{item.get('owner', '')}/{item.get('repo', '')}".strip("/"),
                "sha": item.get("sha") or item.get("commit_sha"),
                "url": item.get("html_url") or item.get("url"),
                "snippet": first_match.get("fragment")
                or item.get("snippet")
                or item.get("content")
                or item.get("matching_line"),
            }
        )

    return normalized


def normalize_file_result(path: str, payload: Any, raw_text: str, result: Any) -> dict[str, Any]:
    item = payload.get("content") if isinstance(payload, dict) and payload.get("content") else payload
    if isinstance(payload, dict) and payload.get("item"):
        item = payload["item"]

    if not isinstance(item, dict):
        item = {}

    resource_text, resource_sha = extract_embedded_resource_text(result)
    status_sha = extract_status_sha(raw_text)
    content = (
        resource_text
        or decode_maybe_base64(item.get("content"), item.get("encoding"))
        or item.get("text")
        or raw_text
    )
    sha = item.get("sha") or status_sha or resource_sha

    if not content:
        raise RuntimeError(f"GitHub MCP returned no file content for path: {path}")

    return {
        "path": path,
        "sha": sha,
        "content": content,
    }


class GitHubMcpClient:
    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def initialize(self) -> ClientSession:
        if self._session:
            return self._session

        require_github_token()
        if not os.getenv("GITHUB_TOOLSETS"):
            os.environ["GITHUB_TOOLSETS"] = "repos"

        command, args = get_server_command_config()
        server = StdioServerParameters(command=command, args=args, env=get_server_environment())
        self._stack = AsyncExitStack()
        read_stream, write_stream = await self._stack.enter_async_context(stdio_client(server))
        self._session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self._session.initialize()

        tools_result = await self._session.list_tools()
        tool_names = {tool.name for tool in tools_result.tools}
        if "search_code" not in tool_names or "get_file_contents" not in tool_names:
            await self.close()
            raise RuntimeError(
                "Connected to MCP server, but required GitHub tools are missing. "
                "Expected search_code and get_file_contents."
            )

        return self._session

    async def close(self) -> None:
        stack = self._stack
        self._stack = None
        self._session = None
        if stack:
            await stack.aclose()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        session = await self.initialize()
        return await session.call_tool(name, arguments)

    async def search_code(self, query: str) -> list[dict[str, Any]]:
        repo = get_repo_config()
        repo_scoped_query = f"repo:{repo['owner']}/{repo['repo']} {query}".strip()
        result = await self.call_tool(
            "search_code",
            {
                "query": repo_scoped_query,
                "perPage": 10,
            },
        )

        raw_text = extract_text_content(result)
        payload = getattr(result, "structured_content", None) or safe_json_parse(raw_text) or {}
        return [
            {
                **item,
                "ref": repo["ref"],
            }
            for item in normalize_search_results(payload)
        ]

    async def get_file(self, path: str) -> dict[str, Any]:
        repo = get_repo_config()
        arguments = {
            "owner": repo["owner"],
            "repo": repo["repo"],
            "path": path,
        }
        if repo["ref"]:
            arguments["ref"] = repo["ref"]

        result = await self.call_tool("get_file_contents", arguments)

        raw_text = extract_text_content(result)
        payload = getattr(result, "structured_content", None) or safe_json_parse(raw_text) or {}
        return normalize_file_result(path, payload, raw_text, result)
