# RLM Usage

CodexOAuth exposes an OpenAI-compatible local API, so RLM can use it through
RLM's custom OpenAI-compatible backend.

Start the proxy:

```bash
cd /Users/navilan/Documents/CodexOAuth
source .venv/bin/activate
python codex_oauth_api.py --host 127.0.0.1 --port 8766
```

Use this base URL from RLM:

```text
http://127.0.0.1:8766/v1
```

For AIReas tests:

```bash
cd /Users/navilan/Documents/AIReas
.venv/bin/python RLMTEST/run_rlmtest.py \
  --backend codex-proxy \
  --codex-proxy-base-url http://127.0.0.1:8766/v1
```

For the structured extraction test:

```bash
cd /Users/navilan/Documents/AIReas
.venv/bin/python RLMTEST/structured-data/run_structured_extraction.py \
  --backend codex-proxy \
  --codex-proxy-base-url http://127.0.0.1:8766/v1
```

If you require a local API key:

```bash
export CODEX_OAUTH_API_KEY="choose-a-local-secret"
python codex_oauth_api.py --host 127.0.0.1 --port 8766
```

Then pass the same key to the AIReas runner:

```bash
--codex-proxy-api-key "$CODEX_OAUTH_API_KEY"
```

Security note: keep the server bound to `127.0.0.1` unless you have added
real authentication and network controls.
