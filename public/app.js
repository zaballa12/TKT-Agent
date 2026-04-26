const form = document.querySelector("#ticket-form");
const ticketInput = document.querySelector("#ticket");
const submitButton = document.querySelector("#submit-button");
const refreshButton = document.querySelector("#refresh-button");
const statusElement = document.querySelector("#status");
const resultElement = document.querySelector("#result");

function setStatus(message) {
  statusElement.textContent = message || "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderTicket(record) {
  const analysis = record.result?.technical_analysis || {};
  const ticketAnalysis = record.result?.ticket_analysis || {};
  const files = analysis.evidence_files || analysis.files_to_check || [];

  resultElement.className = "ticket-result";
  resultElement.innerHTML = `
    <article>
      <div class="meta-row">
        <span>${escapeHtml(record.id)}</span>
        <span>${escapeHtml(ticketAnalysis.problem_type || "sem tipo")}</span>
        <span>confianca: ${escapeHtml(analysis.confidence || "n/a")}</span>
      </div>
      <h3>Resposta sugerida</h3>
      <p>${escapeHtml(analysis.suggested_reply || "Sem resposta sugerida.")}</p>
      <h3>Analise tecnica</h3>
      <p>${escapeHtml(analysis.analysis || "Sem analise tecnica.")}</p>
      <h3>Arquivos de evidencia</h3>
      ${
        files.length
          ? `<ul>${files.map((file) => `<li>${escapeHtml(file)}</li>`).join("")}</ul>`
          : "<p>Nenhum arquivo carregado.</p>"
      }
    </article>
  `;
}

function renderTicketList(tickets) {
  if (!tickets.length) {
    resultElement.className = "empty";
    resultElement.textContent = "Nenhum ticket analisado nesta sessao.";
    return;
  }

  const latest = tickets[0];
  resultElement.className = "ticket-list";
  resultElement.innerHTML = tickets
    .map(
      (ticket) => `
        <button type="button" data-ticket-id="${escapeHtml(ticket.id)}">
          <strong>${escapeHtml(ticket.id)}</strong>
          <span>${escapeHtml(ticket.problem_type || "sem tipo")}</span>
          <small>${escapeHtml(ticket.suggested_reply || "Sem resumo.")}</small>
        </button>
      `
    )
    .join("");

  resultElement.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => loadTicket(button.dataset.ticketId));
  });

  loadTicket(latest.id);
}

async function loadTickets() {
  setStatus("Carregando lista...");
  const response = await fetch("/api/tickets");

  if (!response.ok) {
    throw new Error("Nao foi possivel carregar os tickets.");
  }

  const tickets = await response.json();
  renderTicketList(tickets);
  setStatus("");
}

async function loadTicket(ticketId) {
  setStatus("Carregando ticket...");
  const response = await fetch(`/api/tickets/${encodeURIComponent(ticketId)}`);

  if (!response.ok) {
    throw new Error("Nao foi possivel carregar o ticket.");
  }

  renderTicket(await response.json());
  setStatus("");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const ticket = ticketInput.value.trim();

  if (!ticket) {
    setStatus("Informe o texto do ticket.");
    return;
  }

  submitButton.disabled = true;
  setStatus("Analisando...");

  try {
    const response = await fetch("/api/tickets", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ ticket })
    });
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.error || payload.detail || "Falha ao analisar ticket.");
    }

    ticketInput.value = "";
    renderTicket(payload);
    setStatus("Analise concluida.");
  } catch (error) {
    setStatus(error.message);
  } finally {
    submitButton.disabled = false;
  }
});

refreshButton.addEventListener("click", () => {
  loadTickets().catch((error) => setStatus(error.message));
});

loadTickets().catch((error) => setStatus(error.message));
