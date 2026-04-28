from typing import Any

from llm import (
    CONFIDENCE_THRESHOLD,
    MAX_ANALYSIS_ROUNDS,
    MAX_CONTEXT_FILES,
    plan_ticket_locally,
    run_llm_analysis,
)
from mcp_client import GitHubMcpClient, get_repo_config

# Recebe um caminho de arquivo e retorna um booleano indicando se é um caminho de código útil.
# Ela evita mandar lixo para a IA.
# Isso é importante porque a LLM tem limite de contexto
# e não faz sentido gastar tokens com arquivo gerado,
# binário, cache ou dependência.
def is_useful_code_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    noisy_segments = ("/obj/", "/bin/", "/.git/", "/packages/")
    noisy_suffixes = (".g.cs", ".designer.cs", ".user", ".cache")

    if any(segment in normalized for segment in noisy_segments):
        return False

    if normalized.endswith(noisy_suffixes):
        return False

    return True

# Recebe lista de termos a buscar no repo, um estado compatitlhado do agente, o client MCP para buscar os arquivos e o número máximo de arquivos
# Retorna o número de arquivos carregados, e atualiza o estado do agente com os arquivos encontrados e as observações feitas.
async def prefetch_repository_context(
    search_queries: list[str],
    state: dict[str, Any],
    client: GitHubMcpClient,
    max_files: int = MAX_CONTEXT_FILES,
) -> int:
    await client.initialize()
    loaded_count = 0

    for query in search_queries:
        # Se já carregou arquivos suficientes, não precisa buscar mais
        if len(state["code_context"]) >= max_files:
            return loaded_count

        # Busca código no github usando a query
        matches = await client.search_code(query)
        # Isso cria uma observação dizendo: “fiz uma busca com esta query e encontrei esses resultados”.
        state["observations"].append(
            {
                "type": "search",
                "query": query,
                "matches": matches[:5],
            }
        )
        # Atualiza métricas do uso do MCP.
        state["mcp"]["attempted"] = True
        state["mcp"]["used"] = True
        state["mcp"]["searches"] += 1
        state["mcp"]["tool_observations"] += 1
        
        # Percorre cada resultado encontado da busca (search_code)
        for match in matches:
            # Se já carregou arquivos suficientes, não precisa buscar mais
            if len(state["code_context"]) >= max_files:
                return loaded_count
            # Pega o caminho do arquivo encontrado, 
            # se o caminho não for útil ou já tiver sido visto,
            # pula para o próximo resultado
            path = match["path"]
            if not is_useful_code_path(path):
                continue
            if path in state["seen_paths"]:
                continue
            
            # Busca o conteúdo completo do arquivo
            file = await client.get_file(path)
            # Adiciona o arquivo ao contexto que será enviado para a IA
            state["code_context"].append(file)
            # Marca com já visto
            state["seen_paths"].add(file["path"])
            # Adiciona uma observação dizendo: “carreguei este arquivo, aqui está um preview do conteúdo”
            state["observations"].append(
                {
                    "type": "file",
                    "path": file["path"],
                    "preview": str(file["content"])[:500],
                }
            )
            # Atualiza métricas do uso do MCP.
            state["mcp"]["files_loaded"] += 1
            state["mcp"]["tool_observations"] += 1
            loaded_count += 1
    # Retorna quantos arquivos foram carregados
    return loaded_count

# Recebe lista de termos a buscar no repo, um estado compatitlhado do agente, o client MCP para buscar os arquivos e o número máximo de arquivos
# Retorna o número de arquivos carregados, e atualiza o estado do agente com os arquivos encontrados e as observações feitas.
# A diferença para a função anterior é que essa recebe uma lista de arquivos priorizados pela análise técnica da LLM, e tenta carregar esses arquivos primeiro.
# Isso é importante para tentar trazer o máximo de contexto relevante para a LLM, e evitar gastar tokens com arquivos que não são tão relevantes.
# A função é chamada dentro do loop de análise, ou seja, a cada rodada a LLM pode indicar novos arquivos priorizados para carregar, e essa função vai tentar carregar esses arquivos antes de fazer uma nova rodada de análise.
async def prefetch_prioritized_files(
    paths: list[str],
    state: dict[str, Any],
    client: GitHubMcpClient,
    max_files: int = MAX_CONTEXT_FILES,
) -> int:
    await client.initialize()
    loaded_count = 0
    # Percorre cada caminho de arquivo priorizado
    for path in paths:
        if len(state["code_context"]) >= max_files:
            return loaded_count
        if not is_useful_code_path(path):
            continue
        if path in state["seen_paths"]:
            continue
        # Busca o conteúdo completo do arquivo
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
        # Atualiza métricas do uso do MCP.
        state["mcp"]["attempted"] = True
        state["mcp"]["used"] = True
        state["mcp"]["files_loaded"] += 1
        state["mcp"]["tool_observations"] += 1
        loaded_count += 1
    # Retorna quantos arquivos foram carregados
    return loaded_count

# Função principal que roda o agente de análise de tickets.
# Ela recebe um ticket
# Retorna um dicionário com o resultado da análise, incluindo o contexto de código encontrado, as observações feitas, a análise técnica e as métricas de uso do MCP.
async def run_agent(ticket: str) -> dict[str, Any]:
    # Pega a configuração do repositório (nome, branch, etc) para usar nas buscas de código
    repository = get_repo_config()
    # Inicializa o estado do agente, incluindo as métricas de uso do MCP, o contexto de código encontrado, as observações feitas e os caminhos de arquivos já vistos.
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
    # Inicializa o client do MCP para buscar código no GitHub.
    client = GitHubMcpClient()
    # Faz uma análise inicial do ticket localmente, para extrair informações básicas
    # Isso é para reduuzir o custo de LLM 
    ticket_analysis = plan_ticket_locally(ticket)

    try:
        # Se a análise inicial indicar que precisa de contexto de código e tiver termos de busca, já faz uma pré-busca desses termos antes de começar as rodadas de análise com a LLM.
        if ticket_analysis["needs_code_context"] and ticket_analysis["search_queries"]:
            await prefetch_repository_context(ticket_analysis["search_queries"], state, client)

        result: dict[str, Any] | None = None
        # Tool call loop: a cada rodada, a LLM pode indicar que precisa de mais contexto de código,
        # e quais arquivos ou termos de busca são prioritários para trazer esse contexto.
        # O loop continua até que a LLM indique que tem confiança suficiente para dar uma resposta, 
        # ou que atingiu o número máximo de rodadas, ou que não precisa mais de contexto.
        for round_index in range(1, MAX_ANALYSIS_ROUNDS + 1):
            # Roda a análise com a LLM, passando o ticket,
            # o contexto de código encontrado até agora,
            # as observações feitas 
            # e a análise do ticket da rodada anterior.
            result = await run_llm_analysis(
                ticket=ticket,
                repository=repository,
                execute_tool=None,
                fallback_context={
                    "code_context": code_context,
                    "observations": observations,
                },
                ticket_analysis=ticket_analysis,
                round_index=round_index,
                max_rounds=MAX_ANALYSIS_ROUNDS,
            )
            ticket_analysis = result["ticket_analysis"]
            technical_analysis = result["technical_analysis"]
            
            confidence_score = int(technical_analysis.get("confidence_score") or 0)
            reached_threshold = confidence_score >= CONFIDENCE_THRESHOLD
            reached_limit = round_index >= MAX_ANALYSIS_ROUNDS
            needs_more_context = bool(technical_analysis.get("needs_more_context"))

            # Se confiança está boa, ou o número máximo de rodadas, ou a LLM indicar que não precisa mais de contexto, sai do loop de análise.
            if reached_threshold or reached_limit or not needs_more_context:
                break
            
            loaded_count = 0
            # A LLM indicou que precisa de mais contexto, então vamos tentar trazer mais contexto baseado nas indicações dela.
            # A LLM pode indicar arquivos prioritários para carregar, ou termos de busca adicionais para buscar mais código no repositório.
            prioritized_files = technical_analysis.get("prioritized_files") or []
            additional_search_queries = technical_analysis.get("additional_search_queries") or []

            # Primeiro tenta carregar os arquivos prioritários indicados pela LLM
            if prioritized_files:
                loaded_count += await prefetch_prioritized_files(
                    prioritized_files,
                    state,
                    client,
                )
            # Depois tenta buscar mais código usando os termos de busca adicionais indicados pela LLM
            if additional_search_queries and len(code_context) < MAX_CONTEXT_FILES:
                loaded_count += await prefetch_repository_context(
                    additional_search_queries,
                    state,
                    client,
                )
            # Se não conseguiu carregar nenhum arquivo novo, marca que não precisa mais de contexto para evitar rodar o loop de análise novamente sem ter mais contexto para trazer.
            if loaded_count == 0:
                technical_analysis["needs_more_context"] = False
                break
        # Se saiu do loop sem ter atingido o limite de rodadas, mas também sem ter atingido o limiar de confiança,
        # isso pode indicar que a análise não conseguiu chegar a uma conclusão satisfatória, então podemos considerar isso como um resultado sem sucesso.
        if result is None:
            raise RuntimeError("Ticket analysis did not produce a result.")
        
        # Adiciona ao resultado final as queries de busca que foram executadas, para ter um registro completo do que foi feito durante a análise.
        executed_queries = [
            item["query"] for item in observations if item.get("type") == "search"
        ]
        # Se foram executadas queries de busca, adiciona isso à análise do ticket para ter um registro completo do que foi feito durante a análise.
        if executed_queries:
            ticket_analysis["search_queries"] = executed_queries
        
        # Retorna o resultado final da análise, incluindo o contexto de código encontrado, as observações feitas, a análise técnica e as métricas de uso do MCP.
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
