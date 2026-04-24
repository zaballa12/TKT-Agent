const OPENAI_API_URL = "https://api.openai.com/v1/responses";
const DEFAULT_MODEL = process.env.OPENAI_MODEL || "gpt-5";
const MAX_TOOL_ROUNDS = Number(process.env.OPENAI_MAX_TOOL_ROUNDS || 8);

function delay(value) {
  return new Promise((resolve) => {
    setTimeout(() => resolve(value), 50);
  });
}

function hasOpenAIConfig() {
  return Boolean(process.env.OPENAI_API_KEY);
}

function detectProblemType(normalizedTicket) {
  if (normalizedTicket.includes("error") || normalizedTicket.includes("falha")) {
    return "bug";
  }

  if (
    normalizedTicket.includes("?") ||
    normalizedTicket.includes("como ") ||
    normalizedTicket.includes("gostaria de saber") ||
    normalizedTicket.includes("how ")
  ) {
    return "question";
  }

  return "improvement";
}

function buildSearchPlan(normalizedTicket) {
  if (
    normalizedTicket.includes("duplicado") ||
    normalizedTicket.includes("duplicados") ||
    normalizedTicket.includes("duplicate")
  ) {
    return {
      needsCodeContext: true,
      searchQueries: [
        "duplicate files content hash",
        "duplicate file compare checksum",
        "find duplicate files by content"
      ],
      suspectedAreas: ["duplicate detection", "content hashing", "file scanning"]
    };
  }

  if (
    normalizedTicket.includes("como ") ||
    normalizedTicket.includes("gostaria de saber") ||
    normalizedTicket.includes("how ")
  ) {
    return {
      needsCodeContext: true,
      searchQueries: ["implementation flow entry point", "service logic repository behavior"],
      suspectedAreas: ["implementation flow", "service layer", "repository behavior"]
    };
  }

  if (normalizedTicket.includes("invoice") || normalizedTicket.includes("generic error")) {
    return {
      needsCodeContext: true,
      searchQueries: [
        "invoice generation state registration validation",
        "generic error invoice service exception handling"
      ],
      suspectedAreas: ["invoice generation flow", "customer validation", "error handling"]
    };
  }

  return {
    needsCodeContext: false,
    searchQueries: [],
    suspectedAreas: ["support triage"]
  };
}

function buildObservationSummary(observations) {
  if (!Array.isArray(observations) || observations.length === 0) {
    return "No tool observations were collected.";
  }

  return observations
    .map((item, index) => {
      if (item.type === "search") {
        const topMatches = (item.matches || [])
          .slice(0, 3)
          .map((match) => match.path)
          .join(", ");

        return `${index + 1}. SEARCH query="${item.query}" matches=${item.matches.length}${topMatches ? ` top_paths=${topMatches}` : ""}`;
      }

      if (item.type === "file") {
        return `${index + 1}. FILE path="${item.path}" excerpt="${String(item.preview || "").slice(0, 260)}"`;
      }

      return `${index + 1}. ${JSON.stringify(item)}`;
    })
    .join("\n");
}

function fallbackAnalyzeTicket(ticket) {
  const normalizedTicket = ticket.toLowerCase();
  const problemType = detectProblemType(normalizedTicket);
  const searchPlan = buildSearchPlan(normalizedTicket);

  return {
    problem_type: problemType,
    needs_code_context: searchPlan.needsCodeContext,
    search_queries: searchPlan.searchQueries,
    suspected_areas: searchPlan.suspectedAreas,
    planner_notes: "Fallback local planner used because OPENAI_API_KEY is not configured."
  };
}

function fallbackAnalyzeWithCode(ticketAnalysis, codeContext, observations) {
  const filePaths = codeContext.map((file) => file.path);
  const topFiles = filePaths.slice(0, 3);
  const repoEvidenceFound = filePaths.length > 0;
  const confidence = repoEvidenceFound ? "medium" : "low";

  let analysis;

  if (ticketAnalysis.problem_type === "question" && repoEvidenceFound) {
    analysis =
      "The ticket asks how the implementation works and should be answered with repository context instead of only operational guidance.";
  } else if (ticketAnalysis.problem_type === "question") {
    analysis =
      "The ticket asks how the implementation works, but the current run did not retrieve repository evidence strong enough to explain the implemented behavior safely.";
  } else {
    analysis = "The ticket indicates a technical issue that may require code inspection.";
  }

  if (filePaths.length > 0) {
    analysis += ` The repository search returned relevant files, including ${topFiles.join(", ")}.`;
  } else if (ticketAnalysis.needs_code_context) {
    analysis += " Code context was requested, but no relevant files were returned from the repository search.";
  } else {
    analysis += " No code context was required, so the result is based only on the ticket description.";
  }

  const possibleChanges = [];

  if (ticketAnalysis.problem_type === "question" && filePaths.length > 0) {
    possibleChanges.push("Adicionar documentação curta explicando o fluxo confirmado no repositório.");
  } else if (ticketAnalysis.problem_type === "bug" && filePaths.length > 0) {
    possibleChanges.push("Revisar o fluxo retornado pela busca para localizar o ponto exato da falha.");
    possibleChanges.push("Ajustar o tratamento da causa raiz sem mascarar a exceção original.");
  }

  let devActivity;
  let suggestedReply;
  let nextSteps;
  let recommendedAction;

  if (ticketAnalysis.problem_type === "question") {
    devActivity = "";
    suggestedReply = filePaths.length
      ? `A análise encontrou pontos do código relacionados à funcionalidade consultada. Os arquivos mais relevantes são ${topFiles.join(", ")} e devem ser usados para explicar exatamente como a aplicação se comporta.`
      : "A pergunta é sobre comportamento técnico da aplicação, mas esta execução ainda não encontrou evidência suficiente no repositório para afirmar como o fluxo funciona hoje com segurança.";
    nextSteps = filePaths.length ? [] : ["Refinar a busca no repositório antes de responder conclusivamente."];
    recommendedAction = filePaths.length ? "reply_to_ticket" : "refine_search";
  } else {
    devActivity =
      "Investigar o fluxo retornado pelo repositório, validar a causa raiz e preparar a correção ou explicação técnica adequada.";
    suggestedReply =
      "A análise inicial indica necessidade de revisar o fluxo técnico retornado pela busca para entender o comportamento real da aplicação.";
    nextSteps = [
      "Revisar os arquivos retornados pela busca.",
      "Localizar a decisão técnica central do fluxo.",
      "Definir a correção ou resposta técnica apropriada."
    ];
    recommendedAction = filePaths.length ? "create_dev_task" : "request_more_context";
  }

  return {
    ticket_analysis: ticketAnalysis,
    technical_analysis: {
      analysis,
      suggested_reply: suggestedReply,
      dev_activity: devActivity,
      possible_changes: possibleChanges,
      next_steps: nextSteps,
      files_to_check: filePaths,
      evidence_files: filePaths,
      repo_evidence_found: repoEvidenceFound,
      confidence,
      complexity: filePaths.length >= 3 ? "medium" : "low",
      recommended_action: recommendedAction,
      observation_summary: buildObservationSummary(observations)
    }
  };
}

function getResponseText(payload) {
  if (typeof payload.output_text === "string" && payload.output_text.trim()) {
    return payload.output_text;
  }

  if (!Array.isArray(payload.output)) {
    return "";
  }

  return payload.output
    .flatMap((item) => item.content || [])
    .filter((item) => item.type === "output_text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("");
}

async function createResponse({ input, tools, previousResponseId, toolChoice }) {
  const response = await fetch(OPENAI_API_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${process.env.OPENAI_API_KEY}`
    },
    body: JSON.stringify({
      model: DEFAULT_MODEL,
      previous_response_id: previousResponseId,
      input,
      tools,
      tool_choice: toolChoice
    })
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`OpenAI API error (${response.status}): ${errorText}`);
  }

  return response.json();
}

function buildToolDefinitions() {
  return [
    {
      type: "function",
      name: "search_code",
      description:
        "Search repository code. Use this repeatedly with varied terms in English or Portuguese until you either find strong evidence or conclude evidence is insufficient.",
      strict: true,
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          query: {
            type: "string",
            description: "Repository search query. Use concrete code terms, UI labels, config names, class names, function names, enums, or implementation keywords."
          }
        },
        required: ["query"]
      }
    },
    {
      type: "function",
      name: "get_file_contents",
      description:
        "Read the full contents of a repository file that was found by search_code. Use this to confirm actual implementation details before concluding behavior.",
      strict: true,
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          path: {
            type: "string",
            description: "Exact repository file path."
          }
        },
        required: ["path"]
      }
    },
    {
      type: "function",
      name: "submit_ticket_analysis",
      description:
        "Submit the final structured analysis when you have enough evidence or when you have concluded evidence is insufficient after trying multiple search strategies.",
      strict: true,
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          problem_type: {
            type: "string",
            enum: ["bug", "question", "improvement"]
          },
          needs_code_context: {
            type: "boolean"
          },
          suspected_areas: {
            type: "array",
            items: { type: "string" },
            maxItems: 6
          },
          planner_notes: {
            type: "string"
          },
          analysis: {
            type: "string"
          },
          suggested_reply: {
            type: "string"
          },
          dev_activity: {
            type: "string"
          },
          possible_changes: {
            type: "array",
            items: { type: "string" },
            maxItems: 5
          },
          next_steps: {
            type: "array",
            items: { type: "string" },
            maxItems: 5
          },
          files_to_check: {
            type: "array",
            items: { type: "string" },
            maxItems: 12
          },
          evidence_files: {
            type: "array",
            items: { type: "string" },
            maxItems: 8
          },
          repo_evidence_found: {
            type: "boolean"
          },
          confidence: {
            type: "string",
            enum: ["low", "medium", "high"]
          },
          complexity: {
            type: "string",
            enum: ["low", "medium", "high"]
          },
          recommended_action: {
            type: "string",
            enum: ["reply_to_ticket", "create_dev_task", "request_more_context", "refine_search", "ignore"]
          }
        },
        required: [
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
          "recommended_action"
        ]
      }
    }
  ];
}

function getFunctionCalls(response) {
  return Array.isArray(response.output)
    ? response.output.filter((item) => item.type === "function_call")
    : [];
}

function parseJsonArguments(toolCall) {
  try {
    return JSON.parse(toolCall.arguments || "{}");
  } catch (error) {
    throw new Error(`Invalid tool arguments for ${toolCall.name}: ${toolCall.arguments}`);
  }
}

function buildSystemPrompt(repository) {
  return [
    "You are a technical support agent that must inspect repository evidence before claiming how the system works.",
    `The target repository is ${repository.owner}/${repository.repo}${repository.ref ? ` at ref ${repository.ref}` : ""}.`,
    "You may call search_code and get_file_contents as many times as needed within the available budget.",
    "Do not rely on generic software knowledge when the ticket asks how this repository behaves.",
    "For repository questions, you must try multiple search strategies: user terms, synonyms, English and Portuguese variants, UI labels, enums, config names, and likely implementation terms.",
    "Only mention files in files_to_check or evidence_files if they were actually found in this run.",
    "If evidence is insufficient after several search attempts, set repo_evidence_found=false, confidence=low, and recommended_action=refine_search or request_more_context.",
    "For question tickets, keep dev_activity empty unless a real code change is clearly needed.",
    "For question tickets, possible_changes and next_steps may be empty arrays.",
    "When you are done, call submit_ticket_analysis exactly once with the final structured answer."
  ].join(" ");
}

function buildInitialInput(ticket, repository) {
  return [
    {
      role: "system",
      content: [
        {
          type: "input_text",
          text: buildSystemPrompt(repository)
        }
      ]
    },
    {
      role: "user",
      content: [
        {
          type: "input_text",
          text: `Ticket:\n${ticket}`
        }
      ]
    }
  ];
}

async function runOpenAIToolLoop({ ticket, repository, executeTool }) {
  const tools = buildToolDefinitions();
  let previousResponseId;
  let currentInput = buildInitialInput(ticket, repository);

  for (let round = 0; round < MAX_TOOL_ROUNDS; round += 1) {
    const isLastRound = round === MAX_TOOL_ROUNDS - 1;
    const response = await createResponse({
      input: currentInput,
      tools,
      previousResponseId,
      toolChoice: isLastRound
        ? { type: "function", name: "submit_ticket_analysis" }
        : "auto"
    });

    previousResponseId = response.id;
    const toolCalls = getFunctionCalls(response);

    if (toolCalls.length === 0) {
      const responseText = getResponseText(response);
      throw new Error(
        responseText
          ? `Model ended without submit_ticket_analysis: ${responseText}`
          : "Model ended without tool calls or submit_ticket_analysis."
      );
    }

    const toolOutputs = [];

    for (const toolCall of toolCalls) {
      const args = parseJsonArguments(toolCall);

      if (toolCall.name === "submit_ticket_analysis") {
        return {
          ticket_analysis: {
            problem_type: args.problem_type,
            needs_code_context: args.needs_code_context,
            search_queries: [],
            suspected_areas: args.suspected_areas,
            planner_notes: args.planner_notes
          },
          technical_analysis: {
            analysis: args.analysis,
            suggested_reply: args.suggested_reply,
            dev_activity: args.dev_activity,
            possible_changes: args.possible_changes,
            next_steps: args.next_steps,
            files_to_check: args.files_to_check,
            evidence_files: args.evidence_files,
            repo_evidence_found: args.repo_evidence_found,
            confidence: args.confidence,
            complexity: args.complexity,
            recommended_action: args.recommended_action
          }
        };
      }

      const output = await executeTool(toolCall.name, args);
      toolOutputs.push({
        type: "function_call_output",
        call_id: toolCall.call_id,
        output: JSON.stringify(output)
      });
    }

    currentInput = toolOutputs;
  }

  throw new Error(`Tool loop exceeded ${MAX_TOOL_ROUNDS} rounds without submit_ticket_analysis.`);
}

async function runLLMAnalysis({ ticket, repository, executeTool, fallbackContext }) {
  if (!hasOpenAIConfig()) {
    const ticketAnalysis = fallbackAnalyzeTicket(ticket);
    return delay(
      fallbackAnalyzeWithCode(ticketAnalysis, fallbackContext.codeContext, fallbackContext.observations)
    );
  }

  const result = await runOpenAIToolLoop({
    ticket,
    repository,
    executeTool
  });

  result.technical_analysis.observation_summary = buildObservationSummary(fallbackContext.observations);
  return result;
}

module.exports = {
  buildObservationSummary,
  hasOpenAIConfig,
  runLLMAnalysis
};
