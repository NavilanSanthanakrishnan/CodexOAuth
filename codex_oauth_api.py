from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse


CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
TOKEN_URL = f"{ISSUER}/oauth/token"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
MODELS_CACHE_PATH = CODEX_HOME / "models_cache.json"
PRIVATE_AUTH_FILE = Path(os.environ.get("CODEX_OAUTH_AUTH_FILE", Path.home() / ".codex-oauth" / "auth.json"))
CODEX_CLI_AUTH_FILE = Path(os.environ.get("CODEX_OAUTH_CODEX_CLI_AUTH_FILE", CODEX_HOME / "auth.json"))
DEFAULT_MODEL = os.environ.get("CODEX_OAUTH_MODEL", "gpt-5.4")
DEFAULT_INSTRUCTIONS = os.environ.get("CODEX_OAUTH_INSTRUCTIONS", "You are a concise assistant.")
DEFAULT_REASONING = os.environ.get("CODEX_OAUTH_REASONING", "none").strip().lower()
LOCAL_API_KEY = os.environ.get("CODEX_OAUTH_API_KEY")
REFRESH_SKEW_SECONDS = 120
UPSTREAM_TIMEOUT = float(os.environ.get("CODEX_OAUTH_TIMEOUT", "300"))


class OAuthAPIError(Exception):
    def __init__(self, message: str, status_code: int = 500, error_type: str = "codex_oauth_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type


@dataclass
class Tokens:
    access_token: str
    refresh_token: str = ""
    expires: float = 0.0
    account_id: str = ""
    id_token: str = ""
    source: str = "unknown"
    auth_file: Path | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Codex OAuth Direct API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(OAuthAPIError)
    async def handle_oauth_api_error(_: Request, exc: OAuthAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"message": exc.message, "type": exc.error_type}},
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "codex-oauth-direct-api", "model": DEFAULT_MODEL}

    @app.get("/auth/status")
    async def auth_status(request: Request) -> dict[str, Any]:
        require_local_auth(request)
        tokens = await asyncio.to_thread(resolve_runtime_tokens)
        expires = token_expiry_seconds(tokens.access_token)
        return {
            "logged_in": True,
            "source": tokens.source,
            "auth_file": str(tokens.auth_file) if tokens.auth_file else None,
            "account_id": tokens.account_id or extract_account_id(tokens.access_token, tokens.id_token),
            "expires_at": int(expires) if expires else None,
        }

    @app.get("/v1/models")
    @app.get("/models")
    async def list_models() -> dict[str, Any]:
        return {"object": "list", "data": build_models_payload()}

    @app.post("/v1/responses", response_model=None)
    async def responses(request: Request):
        require_local_auth(request)
        payload = await request.json()
        upstream_payload = build_responses_payload(payload)
        if bool(payload.get("stream")):
            return StreamingResponse(
                stream_responses_api(upstream_payload),
                media_type="text/event-stream",
                headers=sse_headers(),
            )
        response = await asyncio.to_thread(complete_responses_api, upstream_payload)
        return JSONResponse(response)

    @app.post("/v1/chat/completions", response_model=None)
    @app.post("/chat/completions", response_model=None)
    async def chat_completions(request: Request):
        require_local_auth(request)
        payload = await request.json()
        validate_chat_payload(payload)
        upstream_payload = build_chat_as_responses_payload(payload)
        if bool(payload.get("stream")):
            return StreamingResponse(
                stream_chat_completions(upstream_payload, str(payload.get("model") or DEFAULT_MODEL)),
                media_type="text/event-stream",
                headers=sse_headers(),
            )
        response = await asyncio.to_thread(complete_responses_api, upstream_payload)
        return JSONResponse(build_chat_completion_response(response, str(payload.get("model") or DEFAULT_MODEL)))

    return app


def require_local_auth(request: Request) -> None:
    if not LOCAL_API_KEY:
        return
    header = request.headers.get("authorization", "")
    if header != f"Bearer {LOCAL_API_KEY}":
        raise OAuthAPIError("Unauthorized", status_code=401, error_type="invalid_request_error")


def validate_chat_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("messages"), list) or not payload["messages"]:
        raise OAuthAPIError("`messages` must be a non-empty array", status_code=400, error_type="invalid_request_error")


def decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return {}


def extract_account_id(access_token: str, id_token: str = "", fallback: str = "") -> str:
    for token in (id_token, access_token):
        claims = decode_jwt_claims(token)
        if not claims:
            continue
        direct = claims.get("chatgpt_account_id")
        if isinstance(direct, str) and direct:
            return direct
        nested = claims.get("https://api.openai.com/auth")
        if isinstance(nested, dict):
            account_id = nested.get("chatgpt_account_id")
            if isinstance(account_id, str) and account_id:
                return account_id
        orgs = claims.get("organizations")
        if isinstance(orgs, list) and orgs and isinstance(orgs[0], dict):
            org_id = orgs[0].get("id")
            if isinstance(org_id, str) and org_id:
                return org_id
    return fallback


def token_expiry_seconds(token: str) -> float:
    claims = decode_jwt_claims(token)
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and exp > 0:
        return float(exp)
    return 0.0


def is_token_expiring(tokens: Tokens, skew_seconds: int = REFRESH_SKEW_SECONDS) -> bool:
    exp_seconds = token_expiry_seconds(tokens.access_token)
    if exp_seconds:
        return time.time() >= exp_seconds - skew_seconds
    if tokens.expires:
        expires = tokens.expires / 1000.0 if tokens.expires > 10_000_000_000 else tokens.expires
        return time.time() >= expires - skew_seconds
    return False


def read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.expanduser().read_text())
    except (OSError, json.JSONDecodeError):
        return None


def tokens_from_private_store(path: Path) -> Tokens | None:
    data = read_json_file(path)
    if not isinstance(data, dict):
        return None
    access_token = str(data.get("access_token") or "")
    if not access_token:
        return None
    id_token = str(data.get("id_token") or "")
    account_id = extract_account_id(access_token, id_token, str(data.get("account_id") or ""))
    return Tokens(
        access_token=access_token,
        refresh_token=str(data.get("refresh_token") or ""),
        expires=float(data.get("expires") or 0),
        account_id=account_id,
        id_token=id_token,
        source="private-store",
        auth_file=path,
    )


def tokens_from_codex_cli(path: Path) -> Tokens | None:
    data = read_json_file(path)
    if not isinstance(data, dict):
        return None
    raw_tokens = data.get("tokens")
    if not isinstance(raw_tokens, dict):
        return None
    access_token = str(raw_tokens.get("access_token") or "")
    if not access_token:
        return None
    id_token = str(raw_tokens.get("id_token") or "")
    account_id = extract_account_id(access_token, id_token, str(raw_tokens.get("account_id") or ""))
    return Tokens(
        access_token=access_token,
        refresh_token=str(raw_tokens.get("refresh_token") or ""),
        account_id=account_id,
        id_token=id_token,
        source="codex-cli",
        auth_file=path,
    )


def save_private_tokens(path: Path, tokens: Tokens) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires": tokens.expires or (token_expiry_seconds(tokens.access_token) * 1000),
        "account_id": tokens.account_id,
        "id_token": tokens.id_token,
        "source": "device-code",
        "updated_at": int(time.time()),
    }
    path.write_text(json.dumps(payload, indent=2))
    path.chmod(0o600)


def post_form(url: str, fields: dict[str, str], *, timeout: float = 30.0) -> dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "CodexOAuthAPI/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise OAuthAPIError(f"POST {url} failed with HTTP {exc.code}: {body}", status_code=502) from exc
    except urllib.error.URLError as exc:
        raise OAuthAPIError(f"POST {url} failed: {exc}", status_code=502) from exc


def tokens_from_oauth_response(raw: dict[str, Any], fallback_refresh: str = "") -> Tokens:
    access_token = str(raw.get("access_token") or "")
    if not access_token:
        raise OAuthAPIError("OAuth response did not include access_token.", status_code=502)
    refresh_token = str(raw.get("refresh_token") or fallback_refresh or "")
    id_token = str(raw.get("id_token") or "")
    expires_in = float(raw.get("expires_in") or 3600)
    expires = (time.time() + expires_in) * 1000
    account_id = extract_account_id(access_token, id_token)
    return Tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires=expires,
        account_id=account_id,
        id_token=id_token,
        source="private-store",
        auth_file=PRIVATE_AUTH_FILE,
    )


def refresh_tokens(tokens: Tokens, auth_file: Path = PRIVATE_AUTH_FILE) -> Tokens:
    if not tokens.refresh_token:
        raise OAuthAPIError(
            "No refresh token available. Run `/Users/navilan/Documents/CodexOAuth --login` to create a private Codex OAuth session.",
            status_code=401,
            error_type="auth_required",
        )
    raw = post_form(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "client_id": CLIENT_ID,
        },
    )
    refreshed = tokens_from_oauth_response(raw, fallback_refresh=tokens.refresh_token)
    refreshed.auth_file = auth_file
    save_private_tokens(auth_file, refreshed)
    return refreshed


def resolve_runtime_tokens() -> Tokens:
    env_token = os.environ.get("CODEX_AUTH_TOKEN", "").strip()
    if env_token:
        return Tokens(
            access_token=env_token,
            account_id=extract_account_id(env_token),
            source="CODEX_AUTH_TOKEN",
        )

    private = tokens_from_private_store(PRIVATE_AUTH_FILE)
    if private:
        if is_token_expiring(private):
            return refresh_tokens(private, PRIVATE_AUTH_FILE)
        return private

    cli = tokens_from_codex_cli(CODEX_CLI_AUTH_FILE)
    if cli and not is_token_expiring(cli):
        return cli

    raise OAuthAPIError(
        "No valid Codex OAuth token found. Run `/Users/navilan/Documents/CodexOAuth --login` or `codex login` first.",
        status_code=401,
        error_type="auth_required",
    )


def codex_headers(tokens: Tokens) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {tokens.access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "codex_cli_rs/0.0.0 (CodexOAuthAPI)",
        "originator": "codex_cli_rs",
    }
    account_id = tokens.account_id or extract_account_id(tokens.access_token, tokens.id_token)
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    return headers


def load_models_cache() -> list[dict[str, Any]]:
    data = read_json_file(MODELS_CACHE_PATH)
    if not isinstance(data, dict):
        return []
    models = []
    for item in data.get("models", []):
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        if item.get("supported_in_api") is False:
            continue
        if slug == "codex-auto-review":
            continue
        models.append(item)
    return models


def build_models_payload() -> list[dict[str, Any]]:
    now = int(time.time())
    available = [item["slug"] for item in load_models_cache()]
    if not available:
        available = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2"]
    seen: set[str] = set()
    payload = []
    for model_id in available:
        payload.append({"id": model_id, "object": "model", "created": now, "owned_by": "openai-codex-oauth"})
        seen.add(model_id)
    aliases = {"gpt-5": "gpt-5.4", "gpt-4.1": "gpt-5.4", "gpt-5-mini": "gpt-5.4-mini"}
    for alias, target in aliases.items():
        if alias not in seen:
            payload.append({"id": alias, "object": "model", "created": now, "owned_by": f"openai-codex-oauth:{target}"})
    return payload


def extract_text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif item.get("type") in {"image_url", "input_image"}:
                    parts.append("[image]")
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def normalize_responses_input(raw_input: Any) -> list[dict[str, Any]]:
    if raw_input is None:
        return [{"role": "user", "content": ""}]
    if isinstance(raw_input, str):
        return [{"role": "user", "content": raw_input}]
    if isinstance(raw_input, list):
        result: list[dict[str, Any]] = []
        for item in raw_input:
            if isinstance(item, str):
                result.append({"role": "user", "content": item})
            elif isinstance(item, dict):
                if item.get("type") in {"function_call_output", "custom_tool_call_output"}:
                    result.append(item)
                elif isinstance(item.get("role"), str):
                    result.append({"role": item["role"], "content": item.get("content", "")})
                elif item.get("type") in {"message"}:
                    result.append({"role": item.get("role", "user"), "content": item.get("content", "")})
                elif item.get("type") in {"input_text", "output_text"}:
                    result.append({"role": "user", "content": item.get("text", "")})
                else:
                    result.append(item)
        return result or [{"role": "user", "content": ""}]
    return [{"role": "user", "content": str(raw_input)}]


def chat_messages_to_responses(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role in {"system", "developer"}:
            text = extract_text_content(content)
            if text:
                instructions.append(text)
        elif role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id") or message.get("call_id") or "",
                    "output": extract_text_content(content),
                }
            )
        else:
            if role == "assistant" and isinstance(message.get("tool_calls"), list):
                text = extract_text_content(content)
                if text:
                    input_items.append({"role": "assistant", "content": text})
                input_items.extend(chat_tool_calls_to_responses_items(message["tool_calls"]))
                continue
            if role == "assistant" and isinstance(message.get("function_call"), dict):
                text = extract_text_content(content)
                if text:
                    input_items.append({"role": "assistant", "content": text})
                input_items.append(chat_function_call_to_responses_item(message["function_call"]))
                continue
            input_items.append({"role": role, "content": content if content is not None else ""})
    return "\n\n".join(instructions), input_items or [{"role": "user", "content": ""}]


def chat_tool_calls_to_responses_items(tool_calls: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        call_type = str(call.get("type") or "function")
        if call_type != "function":
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        call_id = str(call.get("id") or f"call_{uuid.uuid4().hex[:24]}")
        item_id = call_id if call_id.startswith("fc_") else f"fc_{uuid.uuid4().hex}"
        items.append(
            {
                "type": "function_call",
                "id": item_id,
                "call_id": call_id,
                "name": function.get("name") or "",
                "arguments": function.get("arguments") or "{}",
                "status": "completed",
            }
        )
    return items


def chat_function_call_to_responses_item(function_call: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": f"fc_{uuid.uuid4().hex}",
        "call_id": f"call_{uuid.uuid4().hex[:24]}",
        "name": function_call.get("name") or "",
        "arguments": function_call.get("arguments") or "{}",
        "status": "completed",
    }


def normalize_tools(raw_tools: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw_tools, list):
        return None
    result: list[dict[str, Any]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function" and isinstance(item.get("name"), str):
            result.append(item)
            continue
        if item.get("type") not in {None, "function"}:
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else item
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        result.append(
            {
                "type": "function",
                "name": name,
                "description": function.get("description") or "",
                "parameters": function.get("parameters") or function.get("input_schema") or {"type": "object", "properties": {}},
            }
        )
    return result or None


def apply_common_payload_fields(payload: dict[str, Any], upstream_payload: dict[str, Any]) -> dict[str, Any]:
    tools = normalize_tools(payload.get("tools"))
    if tools:
        upstream_payload["tools"] = tools
        upstream_payload["tool_choice"] = normalize_tool_choice(payload.get("tool_choice", "auto"))
        if "parallel_tool_calls" in payload:
            upstream_payload["parallel_tool_calls"] = bool(payload.get("parallel_tool_calls"))

    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict):
        upstream_payload["reasoning"] = normalize_reasoning(reasoning)
        upstream_payload["include"] = payload.get("include") or ["reasoning.encrypted_content"]
    elif DEFAULT_REASONING in {"low", "medium", "high", "xhigh"}:
        upstream_payload["reasoning"] = {"effort": DEFAULT_REASONING, "summary": "auto"}
        upstream_payload["include"] = ["reasoning.encrypted_content"]
    elif "include" in payload and isinstance(payload.get("include"), list):
        upstream_payload["include"] = payload["include"]

    return upstream_payload


def normalize_tool_choice(tool_choice: Any) -> Any:
    if tool_choice in (None, "auto", "none", "required"):
        return tool_choice or "auto"
    if not isinstance(tool_choice, dict):
        return "auto"
    if tool_choice.get("type") == "function":
        function = tool_choice.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return {"type": "function", "name": function["name"]}
    if tool_choice.get("type") == "custom":
        custom = tool_choice.get("custom")
        if isinstance(custom, dict) and isinstance(custom.get("name"), str):
            return {"type": "custom", "name": custom["name"]}
    return tool_choice


def normalize_reasoning(reasoning: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(reasoning)
    if normalized.get("effort") == "minimal":
        normalized["effort"] = "low"
    return normalized


def build_responses_payload(payload: dict[str, Any]) -> dict[str, Any]:
    model = str(payload.get("model") or DEFAULT_MODEL)
    instructions = str(payload.get("instructions") or DEFAULT_INSTRUCTIONS)
    upstream_payload = {
        "model": model,
        "instructions": instructions,
        "input": normalize_responses_input(payload.get("input")),
        "store": False,
        "stream": True,
    }
    return apply_common_payload_fields(payload, upstream_payload)


def build_chat_as_responses_payload(payload: dict[str, Any]) -> dict[str, Any]:
    model = str(payload.get("model") or DEFAULT_MODEL)
    instructions, input_items = chat_messages_to_responses(payload.get("messages") or [])
    upstream_payload = {
        "model": model,
        "instructions": instructions or DEFAULT_INSTRUCTIONS,
        "input": input_items,
        "store": False,
        "stream": True,
    }
    return apply_common_payload_fields(payload, upstream_payload)


def iter_codex_events(upstream_payload: dict[str, Any], timeout: float = UPSTREAM_TIMEOUT) -> Generator[dict[str, Any], None, None]:
    tokens = resolve_runtime_tokens()
    try:
        yield from _iter_codex_events_with_tokens(upstream_payload, tokens, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 401 and tokens.source == "private-store" and tokens.refresh_token:
            tokens = refresh_tokens(tokens, PRIVATE_AUTH_FILE)
            yield from _iter_codex_events_with_tokens(upstream_payload, tokens, timeout)
            return
        body = exc.read().decode("utf-8", errors="replace")
        raise OAuthAPIError(f"Codex request failed with HTTP {exc.code}: {body}", status_code=502) from exc
    except urllib.error.URLError as exc:
        raise OAuthAPIError(f"Codex request failed: {exc}", status_code=502) from exc


def _iter_codex_events_with_tokens(
    upstream_payload: dict[str, Any],
    tokens: Tokens,
    timeout: float,
) -> Generator[dict[str, Any], None, None]:
    request = urllib.request.Request(
        CODEX_RESPONSES_URL,
        data=json.dumps(upstream_payload).encode("utf-8"),
        headers=codex_headers(tokens),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            if line == "data: [DONE]":
                break
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            yield event


def complete_responses_api(upstream_payload: dict[str, Any]) -> dict[str, Any]:
    final_response: dict[str, Any] = {}
    collected_items: list[dict[str, Any]] = []
    streamed_parts: list[str] = []
    for event in iter_codex_events(upstream_payload):
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                streamed_parts.append(delta)
        elif event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                collected_items.append(item)
        elif event_type == "response.completed":
            response_obj = event.get("response")
            if isinstance(response_obj, dict):
                final_response = response_obj
        elif event_type in {"response.failed", "response.incomplete"}:
            response_obj = event.get("response")
            raise OAuthAPIError(
                f"Codex returned {event_type}: {json.dumps(response_obj, ensure_ascii=False)}",
                status_code=502,
            )

    if not final_response:
        final_response = synthesize_response(upstream_payload, "".join(streamed_parts), collected_items)
    patch_final_response(final_response, "".join(streamed_parts), collected_items)
    return final_response


def synthesize_response(upstream_payload: dict[str, Any], text: str, collected_items: list[dict[str, Any]]) -> dict[str, Any]:
    output = collected_items or [
        {
            "type": "message",
            "id": f"msg_{uuid.uuid4().hex}",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
    ]
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": upstream_payload.get("model", DEFAULT_MODEL),
        "output": output,
        "output_text": text,
    }


def patch_final_response(response: dict[str, Any], streamed_text: str, collected_items: list[dict[str, Any]]) -> None:
    output = response.get("output")
    if (not isinstance(output, list) or not output) and collected_items:
        response["output"] = collected_items
    if not isinstance(response.get("output_text"), str) or not response.get("output_text"):
        response["output_text"] = streamed_text or extract_text_from_response(response)


def extract_text_from_response(response: dict[str, Any]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "output_text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
    return "".join(parts)


def extract_function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    output = response.get("output")
    if not isinstance(output, list):
        return []
    calls = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        calls.append(
            {
                "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": item.get("name") or "",
                    "arguments": item.get("arguments") or "{}",
                },
            }
        )
    return calls


def build_chat_completion_response(response: dict[str, Any], requested_model: str) -> dict[str, Any]:
    tool_calls = extract_function_calls(response)
    content = extract_text_from_response(response)
    message: dict[str, Any] = {"role": "assistant", "content": content}
    finish_reason = "stop" if response.get("status", "completed") == "completed" else "length"
    if tool_calls:
        message["content"] = None
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(response.get("created_at") or time.time()),
        "model": requested_model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": convert_usage_for_chat(response.get("usage")),
    }


def convert_usage_for_chat(raw_usage: Any) -> dict[str, int]:
    if not isinstance(raw_usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = int(raw_usage.get("input_tokens") or raw_usage.get("prompt_tokens") or 0)
    completion = int(raw_usage.get("output_tokens") or raw_usage.get("completion_tokens") or 0)
    total = int(raw_usage.get("total_tokens") or prompt + completion)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def sse_headers() -> dict[str, str]:
    return {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


def sse_bytes(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def done_bytes() -> bytes:
    return b"data: [DONE]\n\n"


def stream_responses_api(upstream_payload: dict[str, Any]) -> Generator[bytes, None, None]:
    try:
        for event in iter_codex_events(upstream_payload):
            yield sse_bytes(event)
        yield done_bytes()
    except OAuthAPIError as exc:
        yield sse_bytes({"type": "error", "error": {"message": exc.message, "type": exc.error_type}})
        yield done_bytes()


def stream_chat_completions(upstream_payload: dict[str, Any], requested_model: str) -> Generator[bytes, None, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    yield sse_bytes(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": requested_model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
    )
    finish_reason = "stop"
    tool_call_index = 0
    try:
        for event in iter_codex_events(upstream_payload):
            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    yield sse_bytes(
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": requested_model,
                            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                        }
                    )
            elif event_type == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "function_call":
                    finish_reason = "tool_calls"
                    current_index = tool_call_index
                    tool_call_index += 1
                    yield sse_bytes(
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": requested_model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": current_index,
                                                "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                                                "type": "function",
                                                "function": {"name": item.get("name") or "", "arguments": item.get("arguments") or "{}"},
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
            elif event_type in {"response.failed", "response.incomplete"}:
                finish_reason = "length"
    except OAuthAPIError as exc:
        yield sse_bytes({"error": {"message": exc.message, "type": exc.error_type}})
        yield done_bytes()
        return

    yield sse_bytes(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": requested_model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }
    )
    yield done_bytes()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenAI-compatible local API backed by direct Codex OAuth.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--reload", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run("codex_oauth_api:create_app", host=args.host, port=args.port, reload=args.reload, factory=True)


if __name__ == "__main__":
    main()
