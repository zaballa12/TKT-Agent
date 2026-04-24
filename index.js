/**
 * README
 *
 * This project runs a local web interface for a ticket analysis agent.
 * The server exposes a small HTTP API, serves a dark-mode frontend, receives
 * tickets, analyzes them with the agent, stores analysis history in memory,
 * and lets the user confirm each analysis from the browser.
 *
 * How to run:
 * 1. Make sure Node.js 18+ is installed.
 * 2. Create a .env file in the project root with GitHub credentials and repo details.
 * 3. Make sure Docker is running, or configure MCP_SERVER_COMMAND/MCP_SERVER_ARGS.
 * 4. Run: npm start
 * 5. Open: http://127.0.0.1:3000
 */

const fs = require("fs");
const path = require("path");
const http = require("http");
const { runAgent } = require("./agent");

const HOST = "127.0.0.1";
const PORT = Number(process.env.PORT || 3000);
const PUBLIC_DIR = path.resolve(__dirname, "..", "public");

const ticketStore = [];

function loadDotEnv() {
  const envPath = path.resolve(__dirname, "..", ".env");

  if (!fs.existsSync(envPath)) {
    return;
  }

  const fileContent = fs.readFileSync(envPath, "utf8");
  const lines = fileContent.split(/\r?\n/);

  for (const line of lines) {
    const trimmedLine = line.trim();

    if (!trimmedLine || trimmedLine.startsWith("#")) {
      continue;
    }

    const separatorIndex = trimmedLine.indexOf("=");

    if (separatorIndex === -1) {
      continue;
    }

    const key = trimmedLine.slice(0, separatorIndex).trim();
    const rawValue = trimmedLine.slice(separatorIndex + 1).trim();
    const value = rawValue.replace(/^["']|["']$/g, "");

    if (key && process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

function createTicketId() {
  const nextNumber = ticketStore.length + 1;
  return `TCK-${String(nextNumber).padStart(4, "0")}`;
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store"
  });
  response.end(JSON.stringify(payload));
}

function sendFile(response, filePath, contentType) {
  try {
    const content = fs.readFileSync(filePath);
    response.writeHead(200, {
      "Content-Type": contentType,
      "Cache-Control": "no-store"
    });
    response.end(content);
  } catch (error) {
    sendJson(response, 404, { error: "File not found." });
  }
}

function getRequestBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";

    request.on("data", (chunk) => {
      body += chunk.toString("utf8");

      if (body.length > 1024 * 1024) {
        reject(new Error("Request body too large."));
        request.destroy();
      }
    });

    request.on("end", () => {
      if (!body) {
        resolve({});
        return;
      }

      try {
        resolve(JSON.parse(body));
      } catch (error) {
        reject(new Error("Invalid JSON body."));
      }
    });

    request.on("error", reject);
  });
}

function summarizeTicket(ticketRecord) {
  return {
    id: ticketRecord.id,
    ticket: ticketRecord.ticket,
    created_at: ticketRecord.created_at,
    status: ticketRecord.status,
    repository: ticketRecord.result.repository,
    problem_type: ticketRecord.result.ticket_analysis.problem_type,
    complexity: ticketRecord.result.technical_analysis.complexity,
    needs_code_context: ticketRecord.result.ticket_analysis.needs_code_context,
    suspected_areas: ticketRecord.result.ticket_analysis.suspected_areas,
    suggested_reply: ticketRecord.result.technical_analysis.suggested_reply
  };
}

async function handleAnalyzeTicket(request, response) {
  try {
    const body = await getRequestBody(request);
    const ticketText = typeof body.ticket === "string" ? body.ticket.trim() : "";

    if (!ticketText) {
      sendJson(response, 400, { error: "The ticket field is required." });
      return;
    }

    const result = await runAgent(ticketText);
    const record = {
      id: createTicketId(),
      ticket: ticketText,
      created_at: new Date().toISOString(),
      status: "analyzed",
      result
    };

    ticketStore.unshift(record);
    sendJson(response, 201, record);
  } catch (error) {
    sendJson(response, 500, {
      error: error.message || "Failed to analyze ticket."
    });
  }
}

function handleListTickets(response) {
  const payload = ticketStore.map(summarizeTicket);
  sendJson(response, 200, payload);
}

function handleGetTicket(response, ticketId) {
  const ticket = ticketStore.find((item) => item.id === ticketId);

  if (!ticket) {
    sendJson(response, 404, { error: "Ticket not found." });
    return;
  }

  sendJson(response, 200, ticket);
}

async function handleConfirmTicket(response, ticketId) {
  const ticket = ticketStore.find((item) => item.id === ticketId);

  if (!ticket) {
    sendJson(response, 404, { error: "Ticket not found." });
    return;
  }

  ticket.status = "confirmed";
  ticket.confirmed_at = new Date().toISOString();
  sendJson(response, 200, ticket);
}

function routeRequest(request, response) {
  const url = new URL(request.url, `http://${request.headers.host || `${HOST}:${PORT}`}`);

  if (request.method === "GET" && url.pathname === "/") {
    sendFile(response, path.join(PUBLIC_DIR, "index.html"), "text/html; charset=utf-8");
    return;
  }

  if (request.method === "GET" && url.pathname === "/app.js") {
    sendFile(response, path.join(PUBLIC_DIR, "app.js"), "application/javascript; charset=utf-8");
    return;
  }

  if (request.method === "GET" && url.pathname === "/styles.css") {
    sendFile(response, path.join(PUBLIC_DIR, "styles.css"), "text/css; charset=utf-8");
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/tickets") {
    handleListTickets(response);
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/tickets") {
    handleAnalyzeTicket(request, response);
    return;
  }

  const ticketMatch = url.pathname.match(/^\/api\/tickets\/([^/]+)$/);
  if (request.method === "GET" && ticketMatch) {
    handleGetTicket(response, ticketMatch[1]);
    return;
  }

  const confirmMatch = url.pathname.match(/^\/api\/tickets\/([^/]+)\/confirm$/);
  if (request.method === "POST" && confirmMatch) {
    handleConfirmTicket(response, confirmMatch[1]);
    return;
  }

  sendJson(response, 404, { error: "Route not found." });
}

function main() {
  loadDotEnv();

  const server = http.createServer(routeRequest);
  server.listen(PORT, HOST, () => {
    console.log(`Server running at http://${HOST}:${PORT}`);
  });
}

main();
