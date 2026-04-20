# How It Works

CodexOAuth uses Codex OAuth tokens to call the ChatGPT Codex backend directly.

## OAuth

The device-code login flow uses:

```text
Issuer: https://auth.openai.com
Device URL: https://auth.openai.com/codex/device
Token URL: https://auth.openai.com/oauth/token
OAuth client ID: app_EMoamEEZ73f0CkXaXp7hrann
```

After login, CodexOAuth stores a private token cache at:

```text
~/.codex-oauth/auth.json
```

If that file does not exist, it can use an existing Codex CLI access token from:

```text
~/.codex/auth.json
```

It does not refresh the Codex CLI token. If you want independent refresh behavior, run:

```bash
./CodexOAuth --login
```

## Backend Call

Requests go directly to the ChatGPT Codex backend:

```text
https://chatgpt.com/backend-api/codex/responses
```

The request uses a Codex OAuth bearer token:

```text
Authorization: Bearer <Codex OAuth access token>
```

It also sends Codex-style headers:

```text
originator: codex_cli_rs
User-Agent: codex_cli_rs/0.0.0 (...)
ChatGPT-Account-ID: <decoded account id>
```

The `ChatGPT-Account-ID` is decoded from the OAuth JWT when available.

## CLI Flow

```text
./CodexOAuth "your prompt"
-> resolve OAuth token
-> POST /backend-api/codex/responses
-> read Server-Sent Events
-> print output text
```

## API Flow

```text
OpenAI-style client
-> http://127.0.0.1:8766/v1/responses
-> codex_oauth_api.py
-> https://chatgpt.com/backend-api/codex/responses
-> streamed Codex response
-> OpenAI-style local response
```

The API supports:

```text
GET  /health
GET  /auth/status
GET  /v1/models
POST /v1/responses
POST /v1/chat/completions
```

## Request Shape

CodexOAuth sends a structured Responses payload:

```json
{
  "model": "gpt-5.4",
  "instructions": "You are a concise assistant.",
  "input": [
    {"role": "user", "content": "your prompt"}
  ],
  "store": false,
  "stream": true
}
```

That direct request shape keeps the CLI and local API small, fast, and easy to inspect.
