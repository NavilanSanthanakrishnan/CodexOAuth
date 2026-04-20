# Setup

## Requirements

- Python 3.10+
- A ChatGPT/Codex account that can use Codex models
- Optional: Codex CLI auth at `~/.codex/auth.json`

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Authenticate

Check current auth:

```bash
./CodexOAuth --status
```

The tool checks token sources in this order:

1. `CODEX_AUTH_TOKEN`
2. `~/.codex-oauth/auth.json`
3. `~/.codex/auth.json`

Create a separate OAuth session:

```bash
./CodexOAuth --login
```

You will see:

```text
https://auth.openai.com/codex/device
```

Open that URL, enter the displayed code, and the tool saves tokens to `~/.codex-oauth/auth.json`.

## Run the CLI

```bash
./CodexOAuth "Reply with exactly: hello"
```

Useful flags:

```bash
./CodexOAuth --model gpt-5.4 "Say hi"
./CodexOAuth --reasoning low "Plan a small API"
./CodexOAuth --no-stream "Return the answer after completion"
./CodexOAuth --json --no-stream "Return raw response metadata"
```

## Run the API

```bash
source .venv/bin/activate
python codex_oauth_api.py --host 127.0.0.1 --port 8766
```

Health check:

```bash
curl http://127.0.0.1:8766/health
```

Auth status:

```bash
curl http://127.0.0.1:8766/auth/status
```

Responses request:

```bash
curl http://127.0.0.1:8766/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.4","input":"Write one sentence about espresso."}'
```

Streaming:

```bash
curl -N http://127.0.0.1:8766/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.4","input":"Count to three.","stream":true}'
```

## Environment

Copy the example:

```bash
cp .env.example .env
```

Then export what you need:

```bash
export CODEX_OAUTH_MODEL="gpt-5.4"
export CODEX_OAUTH_API_KEY="choose-a-local-secret"
export CODEX_OAUTH_AUTH_FILE="$HOME/.codex-oauth/auth.json"
```

The `.env` file is just a reference; the scripts read real environment variables from your shell.
