# Agente de Tickets

Aplicacao Python/FastAPI para analisar tickets tecnicos com Gemini e ferramentas MCP.

## Stack

- Python 3.12
- FastAPI
- Google GenAI SDK
- MCP Python SDK
- GitHub MCP Server via Docker
- Frontend simples em HTML/CSS/JS

## Configuracao

Crie um arquivo `.env` na raiz:

```env
GEMINI_API_KEY=sua_chave_gemini
GEMINI_MODEL=gemini-2.5-flash-lite
MAX_CONTEXT_FILES=6
MAX_FILE_CHARS=12000

GITHUB_TOKEN=seu_token_github
GITHUB_OWNER=usuario_ou_organizacao
GITHUB_REPO=nome_do_repositorio
GITHUB_REF=
```

O projeto aceita `GITHUB_TOKEN` ou `GITHUB_PERSONAL_ACCESS_TOKEN`.

## Consumo de requests

O backend reduz chamadas ao Gemini buscando primeiro o contexto no repositorio via MCP
e depois enviando os arquivos mais relevantes em uma unica requisicao ao modelo.
Isso ajuda quando o gargalo e RPM/RPD, nao TPM.

## Rodando

```powershell
python -m pip install -r requirements.txt
python main.py
```

Acesse:

```text
http://127.0.0.1:3000
```

## MCP

Por padrao, o backend inicia o GitHub MCP Server com Docker:

```text
ghcr.io/github/github-mcp-server stdio --read-only --toolsets=repos
```

O Docker precisa estar instalado e disponivel no terminal. Futuras ferramentas,
como plataforma de tickets e ClickUp, podem ser adicionadas seguindo o mesmo
modelo de tool MCP.
