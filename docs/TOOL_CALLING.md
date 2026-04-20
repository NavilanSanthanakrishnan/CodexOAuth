# Tool Calling

CodexOAuth supports normal model API tool calling:

```text
client sends tool schemas
-> model returns function calls
-> client runs those functions
-> client sends function outputs back
-> model returns the final answer
```

CodexOAuth does not execute arbitrary tools inside the server. That is intentional. Tool execution belongs in the client or agent framework.

## Responses API

First request:

```bash
curl http://127.0.0.1:8766/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-5.4",
    "input": "Use get_weather for San Francisco.",
    "tools": [
      {
        "type": "function",
        "name": "get_weather",
        "description": "Get the weather for a city.",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string"}
          },
          "required": ["city"]
        }
      }
    ],
    "tool_choice": "required"
  }'
```

If the model returns a `function_call`, run the function in your app and send the output back:

```bash
curl http://127.0.0.1:8766/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-5.4",
    "input": [
      {
        "type": "function_call",
        "call_id": "call_abc",
        "name": "get_weather",
        "arguments": "{\"city\":\"San Francisco\"}",
        "status": "completed"
      },
      {
        "type": "function_call_output",
        "call_id": "call_abc",
        "output": "{\"temperature\":\"62F\",\"condition\":\"foggy\"}"
      }
    ]
  }'
```

In a real client, reuse the exact `call_id` returned by the first response.

## Chat Completions

Chat Completions clients can use the OpenAI-style `tools`, `tool_calls`, and `tool` message flow.

First request:

```bash
curl http://127.0.0.1:8766/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-5.4",
    "messages": [
      {"role": "user", "content": "Use get_weather for San Francisco."}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "Get the weather for a city.",
          "parameters": {
            "type": "object",
            "properties": {
              "city": {"type": "string"}
            },
            "required": ["city"]
          }
        }
      }
    ],
    "tool_choice": "required"
  }'
```

Then your client sends back the assistant `tool_calls` and a `tool` message with the output.

## Important

Tool calling support does not mean the model provider runs your tools. It means the provider can request tool calls in a structured format. Your app validates the arguments, runs the tool, and sends the result back.
