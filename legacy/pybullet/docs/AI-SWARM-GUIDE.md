# Real AI mission planning

The optional AI mode calls a language model for each drone's mission decisions.
It selects an exploration frontier, which observed rescue tasks to bid for, and whether to return home.
It receives only that drone's local task memory, position, battery and received peer messages.
Model responses invoke a validated `set_mission_goal` tool. Subsequent observations include the previous goal and task outcomes.
The auction determines the task winner; motor control, obstacle detours, low-battery return and collision separation remain deterministic.
This is a hybrid LLM agent system, not a learned flight controller or visual perception model.

## OpenAI

Set `OPENAI_API_KEY` privately in your PowerShell environment (do not send it in chat).
Then choose a function-calling model your API account supports:

```powershell
cd "C:\Users\tusha\OneDrive\Desktop\simulation"
powershell -ExecutionPolicy Bypass -File .\scripts\legacy\run-disaster.ps1 -AI openai -Model YOUR_MODEL_NAME
```

The launcher checks key/model configuration before opening the simulator.
API requests use HTTPS Responses API function calling, with storage disabled.
Requests contain simulation observations; API usage may incur charges.

## Local Ollama

With Ollama running and a suitable instruction model already downloaded:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\legacy\run-disaster.ps1 -AI ollama -Model YOUR_INSTALLED_MODEL
```

The local adapter calls `http://127.0.0.1:11434/api/chat` with a JSON schema; all responses are validated locally.

## Timing and diagnosis

Flight runs at 48 Hz while at most four inference requests run concurrently.
Each drone plans every 20 wall-clock seconds, or as soon as five seconds when a new goal is needed.
Requests time out after 25 seconds; policies expire after 60 seconds.
The default run permits at most 120 requests. Set `-AIMaxRequests` to adjust this cap.
The model does not produce raw motor RPMs or arbitrary code.
When there is no usable AI goal, search drones hold at their cruise/home goal; there is no silent scripted search fallback.
Existing accepted rescue assignments still execute, and safety return always overrides AI.
The original rule mode remains available by omitting `-AI`.

The HUD shows `AI WAITING / HOLD` until a validated response arrives.
Console `AI:` events and `agentic-ai-events.json` record tool decisions, short reasons and sanitized errors.
Telemetry distinguishes configured backend, accepted decisions, errors and request count.
For network failures inspect your key/model permissions or local server; the log never includes credentials.
When the request cap is reached or decisions expire, the run needs a restart or a larger cap to continue AI search.

Tests use injected model responses and mocked HTTP, not live inference. Live AI operation requires a configured backend.
API format reference: https://developers.openai.com/api/docs/guides/function-calling
