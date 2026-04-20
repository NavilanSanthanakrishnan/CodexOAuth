# Security

CodexOAuth uses OAuth tokens that can access the ChatGPT Codex backend.

Do not commit:

- `~/.codex-oauth/auth.json`
- `~/.codex/auth.json`
- `.env`
- Any copied `access_token` or `refresh_token`

The local API binds to `127.0.0.1` by default. Keep it local unless you know exactly how you want to secure it.

To require a local bearer token for API requests:

```bash
export CODEX_OAUTH_API_KEY="choose-a-local-secret"
python codex_oauth_api.py --host 127.0.0.1 --port 8766
```
