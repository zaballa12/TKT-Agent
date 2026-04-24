const path = require("path");

let clientInstance = null;
let clientPromise = null;

function requireEnv(name) {
  const value = process.env[name];

  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }

  return value;
}

function getRepoConfig() {
  return {
    owner: requireEnv("GITHUB_OWNER"),
    repo: requireEnv("GITHUB_REPO"),
    ref: process.env.GITHUB_REF || undefined
  };
}

function getServerCommandConfig() {
  const command = process.env.MCP_SERVER_COMMAND || "docker";
  const args = process.env.MCP_SERVER_ARGS
    ? process.env.MCP_SERVER_ARGS.split(",").map((part) => part.trim()).filter(Boolean)
    : getDefaultDockerArgs();

  return { command, args };
}

function getDefaultDockerArgs() {
  const args = ["run", "-i"];

  if (process.env.MCP_DOCKER_CONTAINER_NAME) {
    args.push("--name", process.env.MCP_DOCKER_CONTAINER_NAME);
  }

  if (process.env.MCP_DOCKER_KEEP_CONTAINER !== "true") {
    args.push("--rm");
  }

  if (process.env.MCP_DOCKER_VOLUME) {
    args.push("-v", process.env.MCP_DOCKER_VOLUME);
  }

  args.push(
    "-e",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "-e",
    "GITHUB_TOOLSETS",
    "ghcr.io/github/github-mcp-server",
    "stdio",
    "--read-only",
    "--toolsets=repos"
  );

  if (process.env.MCP_ENABLE_COMMAND_LOGGING === "true") {
    const logFile = process.env.MCP_LOG_FILE || "/logs/github-mcp.log";
    args.push("--enable-command-logging", "--log-file", logFile);
  }

  return args;
}

function getServerEnvironment() {
  return {
    ...process.env,
    GITHUB_PERSONAL_ACCESS_TOKEN: requireEnv("GITHUB_PERSONAL_ACCESS_TOKEN"),
    GITHUB_TOOLSETS: process.env.GITHUB_TOOLSETS || "repos"
  };
}

async function createClient() {
  requireEnv("GITHUB_PERSONAL_ACCESS_TOKEN");

  if (!process.env.GITHUB_TOOLSETS) {
    process.env.GITHUB_TOOLSETS = "repos";
  }

  const [{ Client }, { StdioClientTransport }] = await Promise.all([
    import("@modelcontextprotocol/sdk/client/index.js"),
    import("@modelcontextprotocol/sdk/client/stdio.js")
  ]);

  const { command, args } = getServerCommandConfig();
  const env = getServerEnvironment();
  const transport = new StdioClientTransport({
    command,
    args,
    env
  });
  const client = new Client({
    name: "github-ticket-agent",
    version: "1.0.0"
  });

  await client.connect(transport);

  const { tools } = await client.listTools();
  const toolNames = new Set(tools.map((tool) => tool.name));

  if (!toolNames.has("search_code") || !toolNames.has("get_file_contents")) {
    await client.close();
    throw new Error(
      "Connected to MCP server, but required GitHub tools are missing. Expected search_code and get_file_contents."
    );
  }

  return client;
}

async function initializeClient() {
  if (clientInstance) {
    return clientInstance;
  }

  if (!clientPromise) {
    clientPromise = createClient().then((client) => {
      clientInstance = client;
      return client;
    });
  }

  return clientPromise;
}

async function closeClient() {
  if (!clientInstance && !clientPromise) {
    return;
  }

  const client = clientInstance || (await clientPromise);
  clientInstance = null;
  clientPromise = null;
  await client.close();
}

function extractTextContent(result) {
  if (!result || !Array.isArray(result.content)) {
    return "";
  }

  return result.content
    .filter((item) => item.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n");
}

function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch (error) {
    return null;
  }
}

function decodeMaybeBase64(content, encoding) {
  if (!content || encoding !== "base64") {
    return content || "";
  }

  return Buffer.from(content, "base64").toString("utf8");
}

function normalizeSearchResults(payload) {
  const items = Array.isArray(payload)
    ? payload
    : payload?.results || payload?.items || payload?.matches || [];

  return items
    .map((item) => ({
      path: item.path || item.name || "",
      repository:
        item.repository?.full_name ||
        item.repository ||
        `${item.owner || ""}/${item.repo || ""}`.replace(/^\/|\/$/g, ""),
      sha: item.sha || item.commit_sha || null,
      url: item.html_url || item.url || null,
      snippet:
        item.text_matches?.[0]?.fragment ||
        item.snippet ||
        item.content ||
        item.matching_line ||
        null
    }))
    .filter((item) => item.path);
}

function normalizeFileResult(path, payload, rawText) {
  const item = payload?.content ? payload : payload?.item || payload;
  const content =
    decodeMaybeBase64(item?.content, item?.encoding) ||
    item?.text ||
    rawText;

  if (!content) {
    throw new Error(`GitHub MCP returned no file content for path: ${path}`);
  }

  return {
    path,
    sha: item?.sha || null,
    content
  };
}

async function callGitHubTool(name, args) {
  const client = await initializeClient();
  return client.callTool({
    name,
    arguments: args
  });
}

async function searchCode(query) {
  const { owner, repo, ref } = getRepoConfig();
  const repoScopedQuery = `repo:${owner}/${repo} ${query}`.trim();

  const result = await callGitHubTool("search_code", {
    query: repoScopedQuery,
    perPage: 10
  });

  const rawText = extractTextContent(result);
  const payload = result.structuredContent || safeJsonParse(rawText) || {};
  const normalizedResults = normalizeSearchResults(payload);

  return normalizedResults.map((item) => ({
    ...item,
    ref: ref || null
  }));
}

async function getFile(path) {
  const { owner, repo, ref } = getRepoConfig();

  const result = await callGitHubTool("get_file_contents", {
    owner,
    repo,
    path,
    ref
  });

  const rawText = extractTextContent(result);
  const payload = result.structuredContent || safeJsonParse(rawText) || {};

  return normalizeFileResult(path, payload, rawText);
}

module.exports = {
  closeClient,
  getFile,
  getRepoConfig,
  initializeClient,
  searchCode
};
