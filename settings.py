from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from runtime import PROJECT_ROOT, ensure_local_packages

ensure_local_packages()

from dotenv import load_dotenv
import os


@dataclass(frozen=True)
class Settings:
    project_root: Path
    public_dir: Path
    agent_config_dir: Path
    host: str
    port: int
    gemini_api_key: str | None
    gemini_model: str
    max_analysis_rounds_override: int | None
    confidence_threshold_override: int | None
    max_context_files_override: int | None
    max_file_chars_override: int | None
    github_token: str | None
    github_owner: str | None
    github_repo: str | None
    github_ref: str | None
    github_toolsets: str
    mcp_server_command: str
    mcp_server_args: str | None
    mcp_docker_container_name: str | None
    mcp_docker_keep_container: bool
    mcp_docker_volume: str | None
    mcp_enable_command_logging: bool
    mcp_log_file: str | None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    return Settings(
        project_root=PROJECT_ROOT,
        public_dir=PROJECT_ROOT / "public",
        agent_config_dir=PROJECT_ROOT / "config" / "agent",
        host=os.getenv("HOST") or "127.0.0.1",
        port=int(os.getenv("PORT") or "3000"),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL") or "gemini-2.5-flash-lite",
        max_analysis_rounds_override=_parse_optional_int(os.getenv("MAX_ANALYSIS_ROUNDS")),
        confidence_threshold_override=_parse_optional_int(os.getenv("CONFIDENCE_THRESHOLD")),
        max_context_files_override=_parse_optional_int(os.getenv("MAX_CONTEXT_FILES")),
        max_file_chars_override=_parse_optional_int(os.getenv("MAX_FILE_CHARS")),
        github_token=(os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN") or None),
        github_owner=os.getenv("GITHUB_OWNER") or None,
        github_repo=os.getenv("GITHUB_REPO") or None,
        github_ref=os.getenv("GITHUB_REF") or None,
        github_toolsets=os.getenv("GITHUB_TOOLSETS") or "repos",
        mcp_server_command=os.getenv("MCP_SERVER_COMMAND") or "docker",
        mcp_server_args=os.getenv("MCP_SERVER_ARGS") or None,
        mcp_docker_container_name=os.getenv("MCP_DOCKER_CONTAINER_NAME") or None,
        mcp_docker_keep_container=os.getenv("MCP_DOCKER_KEEP_CONTAINER") == "true",
        mcp_docker_volume=os.getenv("MCP_DOCKER_VOLUME") or None,
        mcp_enable_command_logging=os.getenv("MCP_ENABLE_COMMAND_LOGGING") == "true",
        mcp_log_file=os.getenv("MCP_LOG_FILE") or None,
    )


def _parse_optional_int(raw_value: str | None) -> int | None:
    if raw_value is None or raw_value == "":
        return None
    return int(raw_value)
