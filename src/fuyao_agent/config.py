from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


ENV_FILE_VARIABLE = "FUYAO_ENV_FILE"


DEFAULT_MCP_SERVERS = {
    "meta": "/mcp/meta",
    "a-share": "/mcp/a-share",
    "a-share-index": "/mcp/a-share-index",
}


@dataclass(frozen=True)
class Settings:
    modelscope_api_key: str
    modelscope_base_url: str
    modelscope_model: str
    fuyao_api_key: str
    fuyao_base_url: str
    enabled_mcp_servers: tuple[str, ...]
    memory_db_path: str
    env_file: str | None
    report_cache_ttl_seconds: int
    report_cache_similarity_threshold: float


def load_settings() -> Settings:
    env_file = load_project_env()

    enabled_servers = tuple(
        item.strip()
        for item in os.getenv("FUYAO_MCP_SERVERS", "meta,a-share,a-share-index").split(",")
        if item.strip()
    )

    return Settings(
        modelscope_api_key=_required_env("MODELSCOPE_API_KEY"),
        modelscope_base_url=os.getenv(
            "MODELSCOPE_BASE_URL",
            "https://api-inference.modelscope.cn/v1",
        ).rstrip("/"),
        modelscope_model=os.getenv(
            "MODELSCOPE_MODEL",
            "Qwen/Qwen2.5-72B-Instruct",
        ),
        fuyao_api_key=_required_env("FUYAO_API_KEY"),
        fuyao_base_url=os.getenv("FUYAO_BASE_URL", "https://fuyao.aicubes.cn").rstrip("/"),
        enabled_mcp_servers=enabled_servers,
        memory_db_path=load_memory_db_path(),
        env_file=env_file,
        report_cache_ttl_seconds=_env_int("FUYAO_REPORT_CACHE_TTL_SECONDS", 28800),
        report_cache_similarity_threshold=_env_float(
            "FUYAO_REPORT_CACHE_SIMILARITY_THRESHOLD",
            0.78,
        ),
    )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


def load_memory_db_path() -> str:
    load_project_env()
    default_path = Path.cwd() / ".fuyao-memory" / "memory.sqlite3"
    return os.getenv("FUYAO_MEMORY_DB", str(default_path))


def load_project_env() -> str | None:
    explicit_path = os.getenv(ENV_FILE_VARIABLE)
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    cwd_env = Path.cwd() / ".env"
    candidates.append(cwd_env)

    found = find_dotenv(usecwd=True)
    if found:
        candidates.append(Path(found))

    source_root_env = Path(__file__).resolve().parents[2] / ".env"
    candidates.append(source_root_env)

    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return str(candidate)
    return None


def resolve_mcp_urls(base_url: str, server_keys: tuple[str, ...]) -> dict[str, str]:
    unknown = sorted(set(server_keys) - set(DEFAULT_MCP_SERVERS))
    if unknown:
        known = ", ".join(DEFAULT_MCP_SERVERS)
        raise RuntimeError(f"Unknown FUYAO_MCP_SERVERS value(s): {unknown}. Known: {known}")

    return {
        key: f"{base_url}{DEFAULT_MCP_SERVERS[key]}"
        for key in server_keys
    }
