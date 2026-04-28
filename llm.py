# Importa o arquivo bootstrap que carrega .env
import bootstrap  # noqa: F401

import asyncio
import json
import os
import re
from typing import Any, Callable, Awaitable

# Importa o SDK do Gemini
from google import genai
from google.genai import types


DEFAULT_MODEL = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash-lite"
MAX_TOOL_ROUNDS = int(os.getenv("GEMINI_MAX_TOOL_ROUNDS"))
MAX_ANALYSIS_ROUNDS = int(os.getenv("MAX_ANALYSIS_ROUNDS") or os.getenv("GEMINI_MAX_TOOL_ROUNDS"))
CONFIDENCE_THRESHOLD = int(os.getenv("CONFIDENCE_THRESHOLD"))
MAX_CONTEXT_FILES = int(os.getenv("MAX_CONTEXT_FILES"))
MAX_FILE_CHARS = int(os.getenv("MAX_FILE_CHARS"))

# Variável global para reutilizar o cliente Gemini
_gemini_client: genai.Client | None = None

# Verifica se existe configuração do Gemini
def has_gemini_config() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))

# Cria ou retorna o cliente Gemini reutilizando a instância global
def get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _gemini_client


async def delay(value: Any) -> Any:
    await asyncio.sleep(0.05)
    return value


def detect_problem_type(normalized_ticket: str) -> str:
    if "error" in normalized_ticket or "falha" in normalized_ticket:
        return "bug"

    if (
        "?" in normalized_ticket
        or "como " in normalized_ticket
        or "gostaria de saber" in normalized_ticket
        or "how " in normalized_ticket
    ):
        return "question"

    return "improvement"

# Remove duplicados e termos vazios, preservando a ordem original
# Basicamente serve para limpar e organizar os termos extraídos do ticket antes de construir as queries de busca no código
def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result

# Função que recebe o texto do ticket e retorna uma lista de palavras-chave.
def extract_ticket_keywords(ticket: str) -> list[str]:
    words = re.findall(r"[A-Za-zÀ-ÿ0-9_]{3,}", ticket.lower())
    stopwords = {
        "como",
        "funciona",
        "buscar",
        "busca",
        "para",
        "por",
        "com",
        "sem",
        "isso",
        "essa",
        "esse",
        "repositorio",
        "repositório",
        "tipo",
        "dentro",
        "sobre",
        "quais",
        "qual",
        "onde",
    }
    keywords = [word for word in words if word not in stopwords]
    return unique_preserve_order(keywords)[:8]

# Função que constrói uma lista de queries de busca no código a partir do texto do ticket, usando as palavras-chave extraídas e algumas variações.
def build_ticket_driven_queries(ticket: str) -> list[str]:
    keywords = extract_ticket_keywords(ticket)
    phrases: list[str] = []

    if keywords:
        phrases.append(" ".join(keywords[:3]))

    for keyword in keywords:
        phrases.append(keyword)
        if keyword.endswith("s") and len(keyword) > 4:
            phrases.append(keyword[:-1])

    joined = " ".join(keywords)
    if joined:
        phrases.append(joined)

    return unique_preserve_order(phrases)


def build_search_plan(ticket: str, normalized_ticket: str) -> dict[str, Any]:
    ticket_queries = build_ticket_driven_queries(ticket)

    if any(term in normalized_ticket for term in ["duplicado", "duplicados", "duplicate"]):
        duplicate_queries = [
            "duplicado conteudo",
            "duplicados conteudo",
            "buscar duplicados conteudo",
            "buscarduplicadosporconteudo",
            "arquivo duplicado hash",
            "hash conteudo arquivo",
            "duplicate files content hash",
            "duplicate file compare checksum",
            "find duplicate files by content",
        ]
        return {
            "needs_code_context": True,
            "search_queries": unique_preserve_order(ticket_queries + duplicate_queries),
            "suspected_areas": ["duplicate detection", "content hashing", "file scanning"],
        }

    if any(term in normalized_ticket for term in ["como ", "gostaria de saber", "how "]):
        generic_queries = [
            "implementation flow entry point",
            "service logic repository behavior",
        ]
        return {
            "needs_code_context": True,
            "search_queries": unique_preserve_order(ticket_queries + generic_queries),
            "suspected_areas": ["implementation flow", "service layer", "repository behavior"],
        }

    if "invoice" in normalized_ticket or "generic error" in normalized_ticket:
        return {
            "needs_code_context": True,
            "search_queries": unique_preserve_order(ticket_queries + [
                "invoice generation state registration validation",
                "generic error invoice service exception handling",
            ]),
            "suspected_areas": ["invoice generation flow", "customer validation", "error handling"],
        }

    return {
        "needs_code_context": bool(ticket_queries),
        "search_queries": ticket_queries,
        "suspected_areas": ["support triage"],
    }

# Função que constrói um resumo legível das observações coletadas durante a execução das ferramentas, para ajudar o modelo a entender o que foi encontrado no código e nas buscas.
def build_observation_summary(observations: list[dict[str, Any]]) -> str:
    if not observations:
        return "No tool observations were collected."

    lines = []
    for index, item in enumerate(observations, start=1):
        if item.get("type") == "search":
            matches = item.get("matches") or []
            top_matches = ", ".join(match.get("path", "") for match in matches[:3])
            suffix = f" top_paths={top_matches}" if top_matches else ""
            lines.append(
                f'{index}. SEARCH query="{item.get("query")}" matches={len(matches)}{suffix}'
            )
        elif item.get("type") == "file":
            preview = str(item.get("preview") or "")[:260]
            lines.append(f'{index}. FILE path="{item.get("path")}" excerpt="{preview}"')
        else:
            lines.append(f"{index}. {item}")

    return "\n".join(lines)


def fallback_analyze_ticket(ticket: str) -> dict[str, Any]:
    normalized_ticket = ticket.lower()
    problem_type = detect_problem_type(normalized_ticket)
    search_plan = build_search_plan(ticket, normalized_ticket)

    return {
        "problem_type": problem_type,
        "needs_code_context": search_plan["needs_code_context"],
        "search_queries": search_plan["search_queries"],
        "suspected_areas": search_plan["suspected_areas"],
        "planner_notes": "Fallback local planner used because GEMINI_API_KEY is not configured.",
    }


def plan_ticket_locally(ticket: str) -> dict[str, Any]:
    return fallback_analyze_ticket(ticket)

# 
def fallback_analyze_with_code(
    ticket_analysis: dict[str, Any],
    code_context: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    file_paths = [file["path"] for file in code_context]
    top_files = file_paths[:3]
    repo_evidence_found = bool(file_paths)
    confidence = "medium" if repo_evidence_found else "low"

    if ticket_analysis["problem_type"] == "question" and repo_evidence_found:
        analysis = (
            "The ticket asks how the implementation works and should be answered "
            "with repository context instead of only operational guidance."
        )
    elif ticket_analysis["problem_type"] == "question":
        analysis = (
            "The ticket asks how the implementation works, but the current run did "
            "not retrieve repository evidence strong enough to explain the implemented "
            "behavior safely."
        )
    else:
        analysis = "The ticket indicates a technical issue that may require code inspection."

    if file_paths:
        analysis += f" The repository search returned relevant files, including {', '.join(top_files)}."
    elif ticket_analysis["needs_code_context"]:
        analysis += " Code context was requested, but no relevant files were returned from the repository search."
    else:
        analysis += " No code context was required, so the result is based only on the ticket description."

    possible_changes = []
    if ticket_analysis["problem_type"] == "question" and file_paths:
        possible_changes.append("Adicionar documentacao curta explicando o fluxo confirmado no repositorio.")
    elif ticket_analysis["problem_type"] == "bug" and file_paths:
        possible_changes.append("Revisar o fluxo retornado pela busca para localizar o ponto exato da falha.")
        possible_changes.append("Ajustar o tratamento da causa raiz sem mascarar a excecao original.")

    if ticket_analysis["problem_type"] == "question":
        dev_activity = ""
        suggested_reply = (
            f"A analise encontrou pontos do codigo relacionados a funcionalidade consultada. "
            f"Os arquivos mais relevantes sao {', '.join(top_files)} e devem ser usados para "
            "explicar exatamente como a aplicacao se comporta."
            if file_paths
            else "A pergunta e sobre comportamento tecnico da aplicacao, mas esta execucao ainda "
            "nao encontrou evidencia suficiente no repositorio para afirmar como o fluxo funciona "
            "hoje com seguranca."
        )
        next_steps = [] if file_paths else ["Refinar a busca no repositorio antes de responder conclusivamente."]
        recommended_action = "reply_to_ticket" if file_paths else "refine_search"
    else:
        dev_activity = (
            "Investigar o fluxo retornado pelo repositorio, validar a causa raiz e preparar "
            "a correcao ou explicacao tecnica adequada."
        )
        suggested_reply = (
            "A analise inicial indica necessidade de revisar o fluxo tecnico retornado pela "
            "busca para entender o comportamento real da aplicacao."
        )
        next_steps = [
            "Revisar os arquivos retornados pela busca.",
            "Localizar a decisao tecnica central do fluxo.",
            "Definir a correcao ou resposta tecnica apropriada.",
        ]
        recommended_action = "create_dev_task" if file_paths else "request_more_context"

    return {
        "ticket_analysis": ticket_analysis,
        "technical_analysis": {
            "analysis": analysis,
            "suggested_reply": suggested_reply,
            "dev_activity": dev_activity,
            "possible_changes": possible_changes,
            "next_steps": next_steps,
            "files_to_check": file_paths,
            "evidence_files": file_paths,
            "repo_evidence_found": repo_evidence_found,
            "confidence": confidence,
            "confidence_score": 85 if repo_evidence_found else 35,
            "complexity": "medium" if len(file_paths) >= 3 else "low",
            "recommended_action": recommended_action,
            "needs_more_context": not repo_evidence_found,
            "additional_search_queries": [],
            "prioritized_files": [],
            "observation_summary": build_observation_summary(observations),
        },
    }


def build_tool_definitions() -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name="search_code",
            description=(
                "Search repository code. Use this repeatedly with varied terms in English or "
                "Portuguese until you either find strong evidence or conclude evidence is insufficient."
            ),
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Repository search query. Use concrete code terms, UI labels, config names, "
                            "class names, function names, enums, or implementation keywords."
                        ),
                    }
                },
                "required": ["query"],
            },
        ),
        types.FunctionDeclaration(
            name="get_file_contents",
            description=(
                "Read the full contents of a repository file that was found by search_code. "
                "Use this to confirm actual implementation details before concluding behavior."
            ),
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Exact repository file path.",
                    }
                },
                "required": ["path"],
            },
        ),
        types.FunctionDeclaration(
            name="submit_ticket_analysis",
            description=(
                "Submit the final structured analysis when you have enough evidence or when you "
                "have concluded evidence is insufficient after trying multiple search strategies."
            ),
            parametersJsonSchema={
                "type": "object",
                "properties": {
                    "problem_type": {"type": "string", "enum": ["bug", "question", "improvement"]},
                    "needs_code_context": {"type": "boolean"},
                    "suspected_areas": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "planner_notes": {"type": "string"},
                    "analysis": {"type": "string"},
                    "suggested_reply": {"type": "string"},
                    "dev_activity": {"type": "string"},
                    "possible_changes": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "next_steps": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "files_to_check": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                    "evidence_files": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                    "repo_evidence_found": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "recommended_action": {
                        "type": "string",
                        "enum": [
                            "reply_to_ticket",
                            "create_dev_task",
                            "request_more_context",
                            "refine_search",
                            "ignore",
                        ],
                    },
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
                    "complexity",
                    "recommended_action",
                ],
            },
        ),
    ]


def build_system_prompt(repository: dict[str, Any]) -> str:
    ref = f" at ref {repository['ref']}" if repository.get("ref") else ""
    return " ".join(
        [
            "You are a technical support agent that must inspect repository evidence before claiming how the system works.",
            f"The target repository is {repository['owner']}/{repository['repo']}{ref}.",
            "You may call search_code and get_file_contents as many times as needed within the available budget.",
            "Do not rely on generic software knowledge when the ticket asks how this repository behaves.",
            "For repository questions, you must try multiple search strategies: user terms, synonyms, English and Portuguese variants, UI labels, enums, config names, and likely implementation terms.",
            "Only mention files in files_to_check or evidence_files if they were actually found in this run.",
            "If evidence is insufficient after several search attempts, set repo_evidence_found=false, confidence=low, and recommended_action=refine_search or request_more_context.",
            "For question tickets, keep dev_activity empty unless a real code change is clearly needed.",
            "For question tickets, possible_changes and next_steps may be empty arrays.",
            "When you are done, call submit_ticket_analysis exactly once with the final structured answer.",
        ]
    )


def build_initial_contents(ticket: str, repository: dict[str, Any]) -> list[types.Content]:
    return [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    text=f"System instructions:\n{build_system_prompt(repository)}\n\nTicket:\n{ticket}"
                )
            ],
        )
    ]


def build_file_context(code_context: list[dict[str, Any]]) -> str:
    if not code_context:
        return "No repository file contents were loaded."

    chunks = []
    for file in code_context[:MAX_CONTEXT_FILES]:
        content = str(file.get("content") or "")[:MAX_FILE_CHARS]
        chunks.append(f"FILE: {file['path']}\n```text\n{content}\n```")

    return "\n\n".join(chunks)


def build_final_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "problem_type": {"type": "string", "enum": ["bug", "question", "improvement"]},
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
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "confidence_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
            "needs_more_context": {"type": "boolean"},
            "additional_search_queries": {"type": "array", "items": {"type": "string"}},
            "prioritized_files": {"type": "array", "items": {"type": "string"}},
            "recommended_action": {
                "type": "string",
                "enum": [
                    "reply_to_ticket",
                    "create_dev_task",
                    "request_more_context",
                    "refine_search",
                    "ignore",
                ],
            },
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


def build_single_pass_prompt(
    ticket: str,
    repository: dict[str, Any],
    ticket_analysis: dict[str, Any],
    code_context: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    round_index: int,
    max_rounds: int,
) -> str:
    return "\n\n".join(
        [
            f"Repository: {repository['owner']}/{repository['repo']}{f' at ref {repository['ref']}' if repository.get('ref') else ''}",
            f"Analysis round: {round_index} of {max_rounds}",
            f"Target confidence threshold: {CONFIDENCE_THRESHOLD}",
            f"Ticket:\n{ticket}",
            f"Initial local classification:\n{ticket_analysis}",
            f"Tool observations:\n{build_observation_summary(observations)}",
            f"Loaded repository files:\n{build_file_context(code_context)}",
            (
                "Instructions:\n"
                "- Use only the repository evidence provided.\n"
                "- If evidence is insufficient, say so explicitly.\n"
                "- For question tickets, keep dev_activity empty unless code change is clearly required.\n"
                "- Set confidence_score from 0 to 100 based on repository evidence quality.\n"
                "- If confidence_score is below the threshold and more repository inspection may help, set needs_more_context=true.\n"
                "- When needs_more_context=true, suggest focused additional_search_queries and prioritized_files.\n"
                "- Prefer targeted Portuguese and English code search terms when suggesting additional_search_queries.\n"
                "- If this is the final allowed round, answer with the best evidence available and set needs_more_context=false.\n"
                "- Return valid JSON only."
            ),
        ]
    )


def safe_json_parse_response(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Model returned invalid JSON: {text}") from error

    if not isinstance(parsed, dict):
        raise RuntimeError(f"Model returned unexpected JSON payload: {text}")

    return parsed


async def run_gemini_single_pass(
    ticket: str,
    repository: dict[str, Any],
    ticket_analysis: dict[str, Any],
    fallback_context: dict[str, Any],
    round_index: int,
    max_rounds: int,
) -> dict[str, Any]:
    prompt = build_single_pass_prompt(
        ticket=ticket,
        repository=repository,
        ticket_analysis=ticket_analysis,
        code_context=fallback_context["code_context"],
        observations=fallback_context["observations"],
        round_index=round_index,
        max_rounds=max_rounds,
    )

    def call_model() -> types.GenerateContentResponse:
        return get_gemini_client().models.generate_content(
            model=DEFAULT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                responseMimeType="application/json",
                responseJsonSchema=build_final_response_schema(),
            ),
        )

    try:
        response = await asyncio.to_thread(call_model)
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"Gemini SDK error: {error}") from error

    parsed = safe_json_parse_response(response.text or "")
    return {
        "ticket_analysis": {
            "problem_type": parsed.get("problem_type") or ticket_analysis["problem_type"],
            "needs_code_context": parsed.get("needs_code_context", ticket_analysis["needs_code_context"]),
            "search_queries": ticket_analysis.get("search_queries") or [],
            "suspected_areas": parsed.get("suspected_areas") or ticket_analysis["suspected_areas"],
            "planner_notes": parsed.get("planner_notes") or ticket_analysis["planner_notes"],
        },
        "technical_analysis": {
            "analysis": parsed.get("analysis") or "",
            "suggested_reply": parsed.get("suggested_reply") or "",
            "dev_activity": parsed.get("dev_activity") or "",
            "possible_changes": parsed.get("possible_changes") or [],
            "next_steps": parsed.get("next_steps") or [],
            "files_to_check": parsed.get("files_to_check") or [],
            "evidence_files": parsed.get("evidence_files") or [],
            "repo_evidence_found": bool(parsed.get("repo_evidence_found")),
            "confidence": parsed.get("confidence") or "low",
            "confidence_score": int(parsed.get("confidence_score") or 0),
            "complexity": parsed.get("complexity") or "low",
            "needs_more_context": bool(parsed.get("needs_more_context")),
            "additional_search_queries": parsed.get("additional_search_queries") or [],
            "prioritized_files": parsed.get("prioritized_files") or [],
            "recommended_action": parsed.get("recommended_action") or "request_more_context",
        },
    }


def extract_function_calls(response: types.GenerateContentResponse) -> list[Any]:
    return list(response.function_calls or [])


def extract_response_content(response: types.GenerateContentResponse) -> types.Content:
    candidate = response.candidates[0] if response.candidates else None
    if not candidate or not candidate.content:
        return types.Content(role="model", parts=[])
    return candidate.content


def parse_function_args(function_call: Any) -> dict[str, Any]:
    args = getattr(function_call, "args", None)
    return args if isinstance(args, dict) else {}


async def generate_content(
    contents: list[types.Content],
    tools: list[types.FunctionDeclaration],
    forced_function_names: list[str] | None,
) -> types.GenerateContentResponse:
    def call_model() -> types.GenerateContentResponse:
        return get_gemini_client().models.generate_content(
            model=DEFAULT_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                tools=[types.Tool(functionDeclarations=tools)],
                toolConfig=types.ToolConfig(
                    functionCallingConfig=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.ANY,
                        allowedFunctionNames=forced_function_names,
                    )
                )
                if forced_function_names
                else None,
            ),
        )

    try:
        return await asyncio.to_thread(call_model)
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"Gemini SDK error: {error}") from error


async def run_gemini_tool_loop(
    ticket: str,
    repository: dict[str, Any],
    execute_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    tools = build_tool_definitions()
    contents = build_initial_contents(ticket, repository)

    for round_index in range(MAX_TOOL_ROUNDS):
        is_last_round = round_index == MAX_TOOL_ROUNDS - 1
        response = await generate_content(
            contents,
            tools,
            ["submit_ticket_analysis"] if is_last_round else None,
        )
        tool_calls = extract_function_calls(response)

        if not tool_calls:
            response_text = response.text or ""
            raise RuntimeError(
                f"Model ended without submit_ticket_analysis: {response_text}"
                if response_text
                else "Model ended without tool calls or submit_ticket_analysis."
            )

        contents.append(extract_response_content(response))

        for tool_call in tool_calls:
            args = parse_function_args(tool_call)
            name = tool_call.name

            if name == "submit_ticket_analysis":
                return {
                    "ticket_analysis": {
                        "problem_type": args.get("problem_type"),
                        "needs_code_context": args.get("needs_code_context"),
                        "search_queries": [],
                        "suspected_areas": args.get("suspected_areas") or [],
                        "planner_notes": args.get("planner_notes") or "",
                    },
                    "technical_analysis": {
                        "analysis": args.get("analysis") or "",
                        "suggested_reply": args.get("suggested_reply") or "",
                        "dev_activity": args.get("dev_activity") or "",
                        "possible_changes": args.get("possible_changes") or [],
                        "next_steps": args.get("next_steps") or [],
                        "files_to_check": args.get("files_to_check") or [],
                        "evidence_files": args.get("evidence_files") or [],
                        "repo_evidence_found": bool(args.get("repo_evidence_found")),
                        "confidence": args.get("confidence") or "low",
                        "complexity": args.get("complexity") or "low",
                        "recommended_action": args.get("recommended_action") or "request_more_context",
                    },
                }

            output = await execute_tool(name, args)
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            functionResponse=types.FunctionResponse(
                                name=name,
                                id=getattr(tool_call, "id", None),
                                response=output,
                            )
                        )
                    ],
                )
            )

    raise RuntimeError(f"Tool loop exceeded {MAX_TOOL_ROUNDS} rounds without submit_ticket_analysis.")


async def run_llm_analysis(
    ticket: str,
    repository: dict[str, Any],
    execute_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None,
    fallback_context: dict[str, Any],
    ticket_analysis: dict[str, Any] | None = None,
    round_index: int = 1,
    max_rounds: int = MAX_ANALYSIS_ROUNDS,
) -> dict[str, Any]:
    ticket_analysis = ticket_analysis or fallback_analyze_ticket(ticket)

    if not has_gemini_config():
        return await delay(
            fallback_analyze_with_code(
                ticket_analysis,
                fallback_context["code_context"],
                fallback_context["observations"],
            )
        )

    result = await run_gemini_single_pass(
        ticket,
        repository,
        ticket_analysis,
        fallback_context,
        round_index=round_index,
        max_rounds=max_rounds,
    )
    result["technical_analysis"]["observation_summary"] = build_observation_summary(
        fallback_context["observations"]
    )
    return result
