from functools import lru_cache
import asyncio
import json
from typing import Any

from runtime import ensure_local_packages

ensure_local_packages()

from google import genai

from agent_config import AgentConfig, get_agent_config
from settings import get_settings

# Cria ou reutiliza o cliente Gemini. Entra quando `run_llm_analysis` precisa chamar o modelo.
@lru_cache(maxsize=1)
def get_gemini_client() -> genai.Client:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is required to analyze tickets.")
    return genai.Client(api_key=settings.gemini_api_key)

# Criação do prompt
# Resume observações do repositório em texto curto para o prompt.
# - Entra dentro de `build_analysis_prompt`, antes da chamada ao LLM.
# Exemplo:
#    - Input: `[{"type": "search", "query": "auth", "matches": [{"path": "api/auth.py"}]}]`
#    - Output: `SEARCH query="auth" matches=1 top_paths=api/auth.py`
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

# Criação do prompt
# Converte arquivos carregados em blocos de contexto limitados pela configuração.
# - Entra dentro de `build_analysis_prompt` para anexar trechos de código ao prompt.
# Exemplo:
#    - Input: `[{"path": "llm.py", "content": "def x(): pass"}]`
#    - Output: bloco de texto como `FILE: llm.py` seguido pelo conteúdo truncado do arquivo.
def format_file_context(code_context: list[dict[str, Any]], config: AgentConfig) -> str:
    if not code_context:
        return "No repository files were loaded."

    chunks: list[str] = []
    for file in code_context[: config.policy.max_context_files]:
        content = str(file.get("content") or "")[: config.policy.max_file_chars]
        chunks.append(f"FILE: {file['path']}\n```text\n{content}\n```")
    return "\n\n".join(chunks)

# Criação do prompt
# Transforma opções configuráveis em texto para o prompt.
# - Entra em `build_analysis_prompt` para listar categorias e ações válidas.
# Exemplo:
#    - Input: opções com `id="bug"` e `description="Erro funcional"
#    - Output: `- bug: Erro funcional`
def format_option_lines(options: tuple[Any, ...]) -> str:
    return "\n".join(f"- {option.id}: {option.description}" for option in options)

# Criação do prompt
# Monta o prompt final com ticket, contexto do repositório e instruções para o modelo.
# - Entra em `run_llm_analysis` antes de chamar o Gemini.
# Exemplo:
#    - Input: ticket textual, metadados do repositório, observações e arquivos carregados.
#    - Output: string longa com instruções, contexto e campos esperados para análise.
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

# Estrutura saida do prompt
# Define o JSON Schema que diz a IA exatamente quais campos deve retornar, seus tipos e valores permitidos.
# - Entra em `run_llm_analysis` no momento de configurar `generate_content`.
# Exemplo:
#    - Input: config com categorias e ações permitidas.
#    - Output: dict no formato JSON Schema com enums e campos obrigatórios.
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

# Le saida do prompt
# Converte o texto retornado pelo modelo em JSON validado de alto nível.
# - Entra logo após a resposta do Gemini, ainda dentro de `run_llm_analysis`.
# Exemplo:
#    - Input: `'{"problem_type":"bug","confidence_score":80}'`
#    - Output: `{"problem_type": "bug", "confidence_score": 80
def parse_model_payload(response_text: str) -> dict[str, Any]:
    
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Model returned invalid JSON: {response_text}") from error

    if not isinstance(payload, dict):
        raise RuntimeError(f"Model returned unexpected JSON payload: {response_text}")

    return payload

# Le saida do prompt
# Normaliza o payload bruto do modelo para o formato final esperado pela aplicação.
# - Entra em `run_llm_analysis` depois do parse do JSON.
# Exemplo:
#    - Input: payload do modelo + observações + config.
#    - Output: `{"ticket_analysis": {...}, "technical_analysis": {...}}`
def build_analysis_result(
    payload: dict[str, Any],
    observations: list[dict[str, Any]],
    config: AgentConfig,
) -> dict[str, Any]:
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


# Orquestra a análise completa do ticket com prompt, chamada ao LLM e normalização da saída.
# Fluxo:
# 1. Carrega config e settings. 
# 2. Monta o prompt com ticket + contexto do repositório.
# 3. Chama o Gemini com schema JSON.
# 4. Interpreta a resposta e devolve o payload final do agente.
# Exemplo:
# - Input: ticket textual, `repository={"owner":"acme","repo":"api"}`, observações e arquivos.
# - Output: `{"ticket_analysis": {...}, "technical_analysis": {...}}`   
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
                # Vem do arquivo sytem_instruction, que é onde definimos como a IA deve se comportar
                system_instruction=config.system_instruction,
                # Restringe a resposta do modelo a um formato JSON específico, definido por `build_response_schema`.
                responseMimeType="application/json",
                responseJsonSchema=build_response_schema(config),
            ),
        )

    try:
        response = await asyncio.to_thread(call_model)
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"Gemini SDK error: {error}") from error

    payload = parse_model_payload(response.text)
    return build_analysis_result(
        payload=payload,
        observations=observations,
        config=config,
    )
