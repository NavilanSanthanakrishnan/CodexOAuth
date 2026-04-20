# CodexOAuth

Direct ChatGPT Codex OAuth access from a local CLI and an OpenAI-compatible local API.

It sends requests straight to the ChatGPT Codex backend:

```text
your app
-> local CodexOAuth CLI/API
-> https://chatgpt.com/backend-api/codex/responses
-> GPT-5.4
```

It sends structured Responses requests directly and does not require an OpenAI API key. It uses a Codex OAuth access token as a bearer token for the ChatGPT Codex backend.

## What Is Included

- `CodexOAuth`: standalone CLI for sending one request directly to the Codex backend.
- `codex_oauth_api.py`: local FastAPI server exposing OpenAI-style `/v1/responses` and `/v1/chat/completions`.
- `docs/SETUP.md`: setup and usage guide.
- `docs/HOW_IT_WORKS.md`: explanation of the OAuth/backend flow.
- `docs/TOOL_CALLING.md`: function/tool-calling compatibility notes.

## Install

```bash
git clone https://github.com/NavilanSanthanakrishnan/CodexOAuth.git
cd CodexOAuth
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Auth

Fastest path if you already use Codex CLI:

```bash
./CodexOAuth --status
```

If your `~/.codex/auth.json` token is valid, this will work immediately.

Recommended path for a separate app session:

```bash
./CodexOAuth --login
```

That runs the OpenAI Codex device login flow and stores private tokens in:

```text
~/.codex-oauth/auth.json
```

Using a private store avoids refresh-token conflicts with Codex CLI or editor extensions.

## CLI Usage

```bash
./CodexOAuth "Write one sentence about espresso."
```

Use a different reasoning effort:

```bash
./CodexOAuth --reasoning medium "Explain OAuth in two bullets."
```

Return JSON:

```bash
./CodexOAuth --json --no-stream "Reply with exactly: ok"
```

## API Usage

Start the local API:

```bash
source .venv/bin/activate
python codex_oauth_api.py --host 127.0.0.1 --port 8766
```

Use this OpenAI-compatible base URL:

```text
http://127.0.0.1:8766/v1
```

For OpenAI-compatible clients:

```text
Base URL: http://127.0.0.1:8766/v1
API key: anything
Model: gpt-5.4
```

Responses API:

```bash
curl http://127.0.0.1:8766/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-5.4",
    "input": "Write one sentence about espresso."
  }'
```

Chat Completions:

```bash
curl http://127.0.0.1:8766/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-5.4",
    "messages": [
      {"role": "user", "content": "Write one sentence about espresso."}
    ]
  }'
```

Python OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8766/v1",
    api_key="anything",
)

response = client.responses.create(
    model="gpt-5.4",
    input="Write one sentence about espresso.",
)

print(response.output_text)
```

## Local API Auth

By default the local API accepts local requests without a bearer token. To require one:

```bash
export CODEX_OAUTH_API_KEY="choose-a-local-secret"
python codex_oauth_api.py --host 127.0.0.1 --port 8766
```

Then requests need:

```bash
-H "Authorization: Bearer choose-a-local-secret"
```

## More

Read [docs/SETUP.md](docs/SETUP.md) for setup details, [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) for the backend flow, and [docs/TOOL_CALLING.md](docs/TOOL_CALLING.md) for tool-calling behavior.

For RLM integration notes, read [docs/RLM.md](docs/RLM.md).
