# Dota Digital Prison

Local Python setup that watches Dota 2 via **Game State Integration (GSI)**, raises **CODE GREEN** alerts when you break the rules, and exposes an **MCP server** so **Poke** can unlock play time, pardon violations, or terminate the game client.

The watcher never kills Dota on its own for CODE GREEN — only Poke calls `execute_code_green()`.

## Architecture

```
Dota 2 (GSI)  →  watcher.py :3000  →  code_green.json / recent_events.json
Poke (cloud)  →  ngrok        →  mcp_server.py :5000  →  execute / pardon / unlock
```

| Process | Port | Role |
|---------|------|------|
| `watcher.py` | 3000 | GSI listener, rule enforcement, session logs |
| `mcp_server.py` | 5000 | MCP tools for Poke (HTTP + bearer auth) |
| ngrok | 5000 | Public tunnel to MCP (you configure the URL) |

## Enforcement (when prison is locked)

Missing or expired `prison_state.json` → **locked** (enforcement on).

| Rule | Trigger | CODE GREEN reason |
|------|---------|-------------------|
| Forbidden hero | Draft Meepo, Huskar, or Broodmother | `cheese_pick` |
| Feed strikes | 5 deaths within 60s real-time windows | `feeding` |
| Lane grief | First 10 min: off-lane / low XP / low LH vs benchmarks | `lane_grief` |

When unlocked (`unlock_dota`), the watcher still logs violations but does not raise CODE GREEN.

Cheese picks fire **once per match**. The alert stays in `code_green.json` until Poke pardons or executes.

## Project layout

```
TypeShi/
  watcher.py              GSI HTTP server
  mcp_server.py           FastMCP HTTP server
  code_green.py           Alert queue + taskkill (execute only)
  events.py               Violation event log
  lane_map.py             Lane geometry from hero xpos/ypos
  gsi_trace.py            Per-post GSI JSONL trace
  session_log.py          Timestamped logs/ folders
  gamestate_integration_poke.cfg   Copy into Dota cfg folder
  launch_prison.bat       Start watcher + ngrok + MCP
  config/mcp.json.example Optional Cursor MCP template
  .mcp_token.example      Auth token template (copy to .mcp_token)
  logs/                   Session logs (gitignored)
```

## Setup

### 1. Python

```powershell
cd path\to\TypeShi
pip install -r requirements.txt
```

Requires **Python 3.10+** and Dota launched with `-gamestateintegration`.

### 2. GSI config

Copy `gamestate_integration_poke.cfg` to:

```
Steam\steamapps\common\dota 2 beta\game\dota\cfg\gamestate_integration\
```

Add Steam launch option: `-gamestateintegration`

### 3. MCP auth token

```powershell
copy .mcp_token.example .mcp_token
# Edit .mcp_token — one line, UTF-8 (not UTF-16)
setx MCP_AUTH_TOKEN "paste-the-same-token-here"
```

Restart terminals after `setx`. The MCP server reads `MCP_AUTH_TOKEN` or `.mcp_token`.

### 4. Run locally

```powershell
launch_prison.bat
```

Opens three windows: watcher, ngrok, MCP server.

Optional fixed ngrok subdomain:

```powershell
set NGROK_DOMAIN=your-subdomain.ngrok-free.dev
launch_prison.bat
```

### 5. Connect Poke

1. Start ngrok and note the public URL (e.g. `https://xxxx.ngrok-free.dev/mcp/`).
2. In Poke MCP settings:
   - **URL:** `https://YOUR-NGROK-URL/mcp/`
   - **API key:** same value as `.mcp_token`
3. Reconnect MCP after tool changes.

**Recommended:** use `wait_for_code_green(120)` in a loop instead of slow polling — blocks until CODE GREEN or timeout.

### 6. Cursor MCP (optional)

Copy `config/mcp.json.example` to `.cursor/mcp.json` and set your ngrok URL + `${env:MCP_AUTH_TOKEN}`. The `.cursor/` folder is gitignored.

## MCP tools

| Tool | Purpose |
|------|---------|
| `get_prison_status()` | Lock state + active CODE GREEN JSON |
| `get_code_green()` | Active alert only |
| `wait_for_code_green(timeout_seconds=120)` | Block until alert or timeout |
| `execute_code_green()` | **Kill Dota** and clear alert |
| `pardon_code_green(notes="")` | Clear alert without kill; reset feed strikes |
| `unlock_dota(hours=2)` | Write `prison_state.json` unlock window |
| `get_last_prison_violation()` | Latest event from log |
| `get_recent_prison_violations(limit=10)` | Recent events |
| `get_gsi_trace_tail(limit=20)` | Last N lines from latest session trace |

## Logs

Each watcher start creates:

```
logs/YYYY-MM-DD_HHMMSS/
  session.json
  gsi_trace.jsonl    # one JSON line per GSI POST
  watcher.log        # human-readable INFO lines
```

`logs/latest_session.json` points at the most recent run. All of `logs/` is gitignored.

Debug verbosity: `set DOTA_PRISON_LOG=DEBUG` before starting the watcher.

## Security / git

**Never commit:**

- `.mcp_token` — MCP bearer secret
- `.cursor/mcp.json` — may contain tokens
- `logs/`, `code_green.json`, `recent_events.json`, `prison_state.json` — local game data

Use `.mcp_token.example` and `config/mcp.json.example` as templates only.

## Manual run (without bat)

```powershell
# Terminal 1
python watcher.py

# Terminal 2
ngrok http 5000

# Terminal 3
python mcp_server.py
```

MCP binds `127.0.0.1:5000` by default (`MCP_HOST` / `MCP_PORT` to override).
