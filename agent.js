const { runLLMAnalysis } = require("./llm");
const {
  closeClient,
  getFile,
  getRepoConfig,
  initializeClient,
  searchCode
} = require("./mcpClient");

async function fetchCodeContext(searchQueries) {
  const context = [];
  const seenPaths = new Set();
  const observations = [];

  for (const query of searchQueries) {
    const matches = await searchCode(query);
    observations.push({
      type: "search",
      query,
      matches: matches.slice(0, 5)
    });

    for (const match of matches) {
      if (seenPaths.has(match.path)) {
        continue;
      }

      const file = await getFile(match.path);
      context.push(file);
      observations.push({
        type: "file",
        path: file.path,
        preview: String(file.content).slice(0, 500)
      });
      seenPaths.add(match.path);
    }
  }

  return {
    context,
    observations
  };
}

async function executeRepositoryTool(name, args, state) {
  await initializeClient();

  if (name === "search_code") {
    const matches = await searchCode(args.query);
    state.observations.push({
      type: "search",
      query: args.query,
      matches: matches.slice(0, 5)
    });
    state.mcp.attempted = true;
    state.mcp.used = true;
    state.mcp.searches += 1;
    state.mcp.tool_observations += 1;

    return {
      matches: matches.slice(0, 10)
    };
  }

  if (name === "get_file_contents") {
    if (state.seenPaths.has(args.path)) {
      const existingFile = state.codeContext.find((file) => file.path === args.path);

      return {
        path: args.path,
        sha: existingFile?.sha || null,
        content: existingFile?.content || ""
      };
    }

    const file = await getFile(args.path);
    state.codeContext.push(file);
    state.seenPaths.add(file.path);
    state.observations.push({
      type: "file",
      path: file.path,
      preview: String(file.content).slice(0, 500)
    });
    state.mcp.attempted = true;
    state.mcp.used = true;
    state.mcp.files_loaded += 1;
    state.mcp.tool_observations += 1;

    return file;
  }

  throw new Error(`Unsupported repository tool: ${name}`);
}

async function runAgent(ticket) {
  const repository = getRepoConfig();
  const mcp = {
    attempted: false,
    used: false,
    searches: 0,
    files_loaded: 0,
    tool_observations: 0
  };

  let codeContext = [];
  let observations = [];
  let ticketAnalysis;
  let technicalAnalysis;
  const toolState = {
    codeContext,
    observations,
    mcp,
    seenPaths: new Set()
  };

  try {
    const result = await runLLMAnalysis({
      ticket,
      repository,
      executeTool: async (name, args) => executeRepositoryTool(name, args, toolState),
      fallbackContext: {
        codeContext,
        observations
      }
    });

    ticketAnalysis = result.ticket_analysis;
    const executedQueries = observations
      .filter((item) => item.type === "search")
      .map((item) => item.query);
    if (executedQueries.length > 0) {
      ticketAnalysis.search_queries = executedQueries;
    }
    technicalAnalysis = result.technical_analysis;
  } finally {
    await closeClient();
  }

  return {
    repository,
    ticket,
    ticket_analysis: ticketAnalysis,
    mcp,
    observations,
    code_context: codeContext,
    technical_analysis: technicalAnalysis
  };
}

module.exports = {
  runAgent,
  fetchCodeContext
};
