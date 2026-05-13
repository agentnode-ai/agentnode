# Phase 6.0 — AgentNode Guard Design Spec

Status: Approved design, no implementation yet.
Date: 2026-05-13

---

## 1. Ist-Zustand (Current State)

### 1.1 Vorhandene Schichten

| Schicht | Modul | Was es tut | Was fehlt |
|---------|-------|-----------|-----------|
| Install Gate | `policy.check_install()` | Trust-Level + Permission-Dimensionen prüfen | Kein Argument-Check, kein Action-Typ |
| Run Gate | `policy.check_run()` | Trust + Permissions + Environment-Escalation (secrets+network+low trust) | Entscheidung wird nie dem User vorgelegt — `prompt` wird als Fehler zurückgegeben |
| Risk Policies | `policy.check_risk_policies()` | User-konfigurierte Reaktionen auf Risk-Flags (nach allow) | Nur 1 Flag: `external_write_capable` |
| Input Guard | `input_guard.validate_tool_input()` | Path Traversal, URL-Anomalie, Größen-Check | Warning-only, blockt nie |
| Audit | `policy.audit_decision()` | Append-only JSONL, Rotation, env-summary | Kein Realtime-Query, keine Aggregation |
| Agent Limits | `agent_runner.AgentContext` | max_tool_calls, max_iterations, max_runtime_seconds, Allowlist (S4) | Kein Action-Typ-aware Limit |
| MCP Env Filter | `mcp_runner._mcp_env()` | Nur safe_keys im Subprocess-Environment | Keine Argument-Inspektion |
| Python Subprocess | `python_runner._filtered_env()` | Env-Allowlist, isoliertes tmpdir, auto→subprocess | — |
| Remote Runner | `remote_runner` | Domain-Validation, CredentialHandle, Audit | — |

### 1.2 Execution Flow (heute)

```
run_tool(slug, tool_name, **kwargs)
  → _get_lockfile_entry(slug)
  → _maybe_refresh_trust(slug, entry)        # best-effort, fail-open
  → check_run(slug, tool_name, kwargs, entry) # trust + permissions + env
  → audit_decision(decision)
  → check_risk_policies(slug, entry)          # nach allow
  → validate_tool_input(slug, tool_name, kwargs, entry)  # warning-only
  → dispatch:
      python_runner.run_python()    # subprocess mit env-filter
      mcp_runner.run_mcp()          # stdio JSON-RPC, kein Argument-Check
      remote_runner.run_remote()    # domain-validation, credential-handle
      agent_runner.run_agent()      # allowlist, limits, LLM-binding
```

### 1.3 Permission-Dimensionen (config.json)

```
network:        allow | prompt | deny     (default: prompt)
filesystem:     allow | prompt | deny     (default: prompt)
code_execution: sandboxed | prompt | deny (default: sandboxed)
```

Package-Werte: `network_level`, `filesystem_level`, `code_execution_level`

### 1.4 Was heute NICHT existiert

1. Keine Argument-Inspektion für MCP — `call_tool(name, args)` leitet alles weiter
2. Keine Action-Klassifikation — alle Tool-Calls sind gleich, egal ob read oder delete
3. Keine echte User-Confirmation — `prompt` wird als `policy_prompt` Fehler zurückgegeben, kein UI-Dialog
4. Kein Rate Limiting — unbegrenzte Calls pro Zeiteinheit
5. Keine Dangerous Action Detection — Datei löschen, E-Mail senden, API-Key nutzen werden nicht erkannt
6. Keine per-Tool Policy — alles ist per-Package
7. Input Guard blockt nie — nur Warnings
8. Nur 1 Risk-Flag — `external_write_capable`

---

## 2. Guard-Ziele

### Primärziel

Pre-Execution Policy Gateway für alle ausführbaren Package-Typen (Toolpack, MCP Toolpack, Agent). Entscheidungsschicht, die zwischen `run_tool()` und dem tatsächlichen Dispatch sitzt.

### Designprinzipien

1. **Fail-closed**: Unbekannte Actions → deny (im strict Mode) oder prompt (im default Mode)
2. **Additive**: Guard erweitert bestehende `check_run()`-Pipeline, ersetzt sie nicht
3. **Zero-Config sicher**: Default-Policy muss ohne Konfiguration sinnvoll sein
4. **Audit-first**: Jede Guard-Entscheidung wird geloggt, bevor sie wirkt
5. **Kein Performance-Kill**: Guard-Check muss <1ms sein (in-memory, keine I/O)

### Scope

| In Scope | Out of Scope |
|----------|-------------|
| Toolpacks (python, mcp, remote) | Skills (passiv seit Phase 5.1) |
| Agents (via `AgentContext.run_tool`) | Backend-seitige Validation (bereits vorhanden) |
| MCP Tool Argument Inspection | UI für Confirmation Gates (SDK-seitig nur Callback-API) |
| Runtime Rate Limits | Sandbox/Container-Isolation (separater Track) |

---

## 3. Action-Taxonomie

### 3.1 Action-Kategorien

| Kategorie | Risk | Beispiele | Default Policy |
|-----------|------|-----------|---------------|
| `read` | low | Daten lesen, API abfragen, Status prüfen | allow |
| `compute` | low | Berechnung, Transformation, Parsing | allow |
| `write_local` | medium | Datei schreiben, DB insert, lokaler State | allow (wenn filesystem erlaubt) |
| `write_external` | high | E-Mail senden, Slack-Nachricht, API-Write | prompt |
| `delete` | high | Datei löschen, DB drop, Ressource entfernen | prompt |
| `execute` | high | Shell-Befehl, Subprocess, Code-Eval | prompt (wenn code_execution erlaubt) |
| `credential_use` | high | API-Key nutzen, OAuth-Token einsetzen | allow nur wenn Connector/Credential-Scope explizit deklariert UND durch Policy erlaubt; sonst prompt |
| `network_egress` | medium | HTTP-Request, Webhook, DNS-Lookup | allow (wenn network erlaubt) |
| `unknown` | medium | Nicht klassifizierbar | prompt (strict: deny) |

### 3.2 Wie wird die Kategorie bestimmt?

Drei Quellen, in Prioritätsreihenfolge:

**1. Manifest-Deklaration** (vertrauenswürdigste Quelle):
```yaml
capabilities:
  tools:
    - name: delete-file
      action_type: delete          # ← explizit vom Publisher
```

**2. Name-Heuristik** (Fallback):
```
delete*, remove*, drop*, purge*       → delete
send*, post*, publish*, notify*       → write_external
create*, write*, update*, set*        → write_local
get*, read*, list*, fetch*, search*   → read
run*, exec*, eval*, shell*            → execute
```

**3. Permission-Signal** (zusätzliche Escalation):
```
network_level != none                 → mindestens network_egress
code_execution != none                → mindestens execute
connector.auth_type vorhanden         → credential_use Flag
```

### 3.3 Composite Actions

Ein Tool-Call kann mehrere Kategorien gleichzeitig haben. Beispiel: `send-email` = `write_external` + `credential_use` + `network_egress`. Die höchste Risk-Kategorie bestimmt die Policy-Entscheidung.

---

## 4. Risk-Level-Matrix

| Risk Level | Score Range | Trigger | Guard-Reaktion |
|-----------|-------------|---------|---------------|
| low | 0–20 | read, compute | allow |
| medium | 21–45 | write_local, network_egress, unknown | allow + audit |
| high | 46–70 | write_external, delete, execute, credential_use | prompt (oder deny bei strict) |
| critical | 71–100 | high + unverified trust + secrets in env | deny |

### Risk-Score-Berechnung (Erweiterung von risk_profile.py)

Bestehende Signals bleiben. Neue Signals:

| Signal | Punkte | Bedingung |
|--------|--------|-----------|
| `action_type: delete` | +20 | Tool deklariert oder inferiert als delete |
| `action_type: write_external` | +15 | Tool sendet Daten nach außen |
| `action_type: execute` | +15 | Tool führt Code aus |
| `unverified + high_action` | +20 | Unverified Package + high-risk Action |
| `secrets_env + network_egress` | +15 | Secrets im Env + ausgehende Netzwerk-Calls |
| `mcp_untyped_args` | +5 | MCP Tool ohne input_schema |

---

## 5. Permission Enforcement Modell

### 5.1 Schichtenmodell

```
Schicht 1: check_install()         [EXISTIERT] — Trust + Permissions beim Install
Schicht 2: check_run()             [EXISTIERT] — Trust + Permissions + Env beim Run
Schicht 3: check_risk_policies()   [EXISTIERT] — User Risk-Flag Config
Schicht 4: guard.check_action()    [NEU]       — Action-Type + Argument-Inspection
Schicht 5: guard.rate_limit()      [NEU]       — Frequency Cap
```

Jede Schicht kann: `allow` → nächste Schicht, `deny` → sofort stoppen, `prompt` → Confirmation Gate.

### 5.2 Erweiterte Execution-Pipeline

```
run_tool(slug, tool_name, **kwargs)
  → _get_lockfile_entry(slug)
  → _maybe_refresh_trust(slug, entry)
  → check_run(slug, tool_name, kwargs, entry)              # Schicht 2 [existiert]
  → check_risk_policies(slug, entry)                        # Schicht 3 [existiert]
  → guard.check_action(slug, tool_name, kwargs, entry)      # Schicht 4 [NEU]
  → guard.check_rate_limit(slug, tool_name)                 # Schicht 5 [NEU]
  → validate_tool_input(slug, tool_name, kwargs, entry)     # [existiert, wird härter]
  → dispatch
```

---

## 6. Install-Time Checks

### Was bereits existiert

- `check_install()`: Trust-Level ≥ Minimum, Permission-Dimensionen gegen Config

### Was Guard hinzufügt

Nichts im MVP. Install-Time ist bereits gut abgedeckt. Guard fokussiert auf Runtime.

**Post-MVP Option**: Install-Time Risk-Preview — beim `agentnode install` die Action-Typen aller Tools anzeigen, bevor installiert wird.

---

## 7. Runtime Checks (Schicht 4: guard.check_action)

### 7.1 Eingabe

```python
@dataclass
class GuardDecision:
    action: str               # "allow", "deny", "prompt"
    reason: str
    action_types: list[str]   # ["write_external", "credential_use"]
    risk_level: str           # "low", "medium", "high", "critical"
    mitigations: list[str]    # was der User tun kann bei prompt/deny

def check_action(
    slug: str,
    tool_name: str | None,
    kwargs: dict,
    entry: dict,
    *,
    interactive: bool = True,
) -> GuardDecision:
```

### 7.2 Entscheidungslogik

```
1. Classify action_types from manifest + name heuristic + permissions
2. Compute risk_level from action_types + trust + environment
3. Load guard_policy from config (or defaults)
4. For each action_type:
     if action_type == "credential_use":
       if connector/credential scope explicitly declared AND policy allows → allow
       else → prompt (or deny in strict mode)
     elif guard_policy[action_type] == "deny" → deny
     elif guard_policy[action_type] == "prompt" → prompt
     elif guard_policy[action_type] == "allow" → continue
5. If risk_level == "critical" → deny (unoverridable)
6. Return allow
```

### 7.3 Default Guard Policy

```json
{
  "guard": {
    "delete": "prompt",
    "write_external": "prompt",
    "execute": "prompt",
    "credential_use": "prompt",
    "network_egress": "allow",
    "write_local": "allow",
    "read": "allow",
    "compute": "allow",
    "unknown": "prompt"
  }
}
```

Note: `credential_use` defaults to `prompt`. It is promoted to `allow` only when the package explicitly declares a Connector or Credential scope AND the user's policy permits it. This prevents undeclared credential access from silently succeeding.

---

## 8. MCP Tool Argument Inspection

### 8.1 Problem

MCP tools empfangen beliebiges JSON via `tools/call`. Heute:
```python
# mcp_runner.py:73
def call_tool(self, name: str, args: dict, timeout: float = 30.0) -> Any:
    req = {"method": "tools/call", "params": {"name": name, "arguments": args}}
```

Keine Prüfung der `args` bevor sie an den Server gehen.

### 8.2 Lösung

Guard interceptet MCP-Arguments VOR dem `call_tool()`:

```python
# Neuer Hook in run_mcp() — VOR server.call_tool()
guard_decision = guard.inspect_mcp_args(slug, name, args, entry)
if guard_decision.action == "deny":
    return RunToolResult(success=False, error=guard_decision.reason, ...)
```

### 8.3 Was wird geprüft?

| Pattern | Prüfung | Reaktion |
|---------|---------|----------|
| Path Traversal | `../` in String-Feldern | deny |
| Absolute Path Escape | Absolute Pfade außerhalb erlaubter Wurzeln | deny |
| Oversized Arguments | >1MB JSON-Payload | deny |
| URL in non-network Package | HTTP/S URL aber `network_level=none` | deny |
| Shell Injection Markers | `;`, `&&`, `\|` in command-artigen Feldern | prompt or audit (not deny) |
| SQL Injection Markers | `DROP TABLE`, `; DELETE` in String-Feldern | prompt |
| Prompt Injection | `ignore previous`, `system:` in LLM-input-artigen Feldern | audit + allow |

Design principle: Default-deny only for clear technical violations (path traversal, absolute path escape, oversized payloads, URL anomaly). Heuristic-based detections (shell tokens, SQL markers) use prompt or audit — they have false-positive risk and must not block legitimate use in the MVP.

### 8.4 Schema-Aware Inspection

Wenn das MCP-Tool ein `input_schema` im Manifest deklariert hat:
- Typ-Validierung gegen Schema
- Required-Fields Prüfung
- Enum-Werte Einschränkung

Wenn kein Schema vorhanden: höherer Risk-Score (`mcp_untyped_args` Signal).

---

## 9. Audit Logging Modell

### 9.1 Bestehend

```json
{
    "ts": "...",
    "event": "run_tool",
    "slug": "...",
    "tool_name": "...",
    "action": "allow",
    "source": "default",
    "reason": "...",
    "trust": "verified",
    "env": "win32/user/no_ci/no_secrets",
    "request_id": null
}
```

Valid events: `run_tool`, `mcp_run`, `agent_run`, `remote_run`, `client_install`, `runtime_run`

### 9.2 Guard-Erweiterung

Neue Felder im Audit-Record:

```json
{
    "guard_action_types": ["write_external", "credential_use"],
    "guard_risk_level": "high",
    "guard_source": "action_policy.write_external",
    "guard_args_inspected": true,
    "guard_args_findings": ["path_traversal"]
}
```

### 9.3 Neues Event: guard_check

```python
_VALID_EVENTS = frozenset({
    "run_tool", "runtime_run", "client_install", "mcp_run",
    "agent_run", "remote_run",
    "guard_check",      # NEU — Guard-Entscheidung
    "guard_rate_limit", # NEU — Rate Limit Hit
})
```

### 9.4 Audit-Prinzipien (unverändert)

- Append-only, UTF-8, JSONL
- Keine Secrets (nur `has_secrets` bool)
- `details` Feld NICHT in Audit geschrieben (BD-12)
- Rotation konfigurierbar (default: 10MB, 5 Files)
- Schreibfehler crashen nie den Caller

---

## 10. User Confirmation Gates

### 10.1 Problem heute

`check_run()` gibt `PolicyResult(action="prompt")` zurück, aber `runner.py` behandelt das als Fehler:
```python
# runner.py:169
if decision.action == "prompt":
    return RunToolResult(
        success=False,
        error=f"Policy requires approval: {decision.reason}",
        mode_used="policy_prompt",
    )
```

Kein tatsächlicher Prompt an den User.

### 10.2 Lösung: Confirmation Callback

```python
# Neuer Parameter in run_tool()
def run_tool(
    slug: str,
    tool_name: str | None = None,
    *,
    confirmation_callback: Callable[[GuardDecision], bool] | None = None,
    **kwargs: Any,
) -> RunToolResult:
```

Wenn `confirmation_callback` gesetzt ist und Guard/Policy `prompt` zurückgibt:
1. Guard ruft `confirmation_callback(decision)` auf
2. Callback gibt `True` (User bestätigt) oder `False` (User lehnt ab) zurück
3. Bei True: Execution fortsetzen, Audit-Event: `action=allow, source=user_confirmed`
4. Bei False: Deny, Audit-Event: `action=deny, source=user_rejected`

Wenn `confirmation_callback` NICHT gesetzt ist:
- Interactive Mode: Fehler wie heute (caller muss handling implementieren)
- Non-Interactive Mode: deny (fail-closed, wie `AGENTNODE_GUARD_STRICT`)

### 10.3 CLI-Integration (Post-MVP)

```python
# Beispiel CLI-Callback
def cli_confirm(decision: GuardDecision) -> bool:
    print(f"Guard: {decision.reason}")
    print(f"  Action types: {decision.action_types}")
    print(f"  Risk: {decision.risk_level}")
    return input("Continue? [y/N] ").lower() == "y"
```

### 10.4 Agent-Context Integration

Agents rufen Tools über `AgentContext.run_tool()` auf. Agents können nicht interaktiv prompten — ein Agent der bei jedem Tool-Call auf User-Input wartet ist nicht praktikabel.

Lösung: **pre_approved_actions** Modell.

- Agents dürfen nur `action_types` ausführen, die in ihrer **Allowlist** oder in **`pre_approved_actions`** (aus Manifest oder Config) deklariert sind.
- Nicht deklarierte high-risk Actions führen zu `prompt`. Da Agents non-interactive sind, wird `prompt` zu **deny** aufgelöst.
- `critical` Actions (risk score >70) werden **immer denied**, auch wenn sie in `pre_approved_actions` stehen.

```yaml
# Beispiel Agent-Manifest
agent:
  pre_approved_actions:
    - read
    - compute
    - write_local
    - network_egress
```

```json
// Beispiel Config-Override
{
  "guard": {
    "agent_overrides": {
      "my-agent-pack": {
        "pre_approved_actions": ["read", "compute", "write_local"]
      }
    }
  }
}
```

Agents behalten ihren bestehenden Trust-Gate (`trusted` minimum) und Allowlist (S4). Guard ergänzt eine zusätzliche Action-Type-Schicht, die sicherstellt dass auch trusted Agents keine unerwarteten high-risk Actions ausführen.

---

## 11. Rate Limits

### 11.1 Design

In-Memory Sliding Window Counter pro `(slug)` Tuple.

```python
@dataclass
class RateLimitConfig:
    calls_per_minute: int = 60
    calls_per_hour: int = 1000
    burst_size: int = 10       # max calls in 1 second
```

### 11.2 Defaults

| Package Type | calls/min | calls/hour | burst |
|-------------|-----------|------------|-------|
| toolpack | 60 | 1000 | 10 |
| mcp toolpack | 60 | 1000 | 10 |
| agent (gesamt) | 120 | 2000 | 20 |

### 11.3 Override via Config

```json
{
  "guard": {
    "rate_limits": {
      "default": {"calls_per_minute": 60},
      "csv-analyzer-pack": {"calls_per_minute": 120}
    }
  }
}
```

### 11.4 Reaktion bei Limit

- Audit-Event `guard_rate_limit` wird geschrieben
- `RunToolResult(success=False, error="Rate limit exceeded", mode_used="guard_rate_limited")`
- Kein retry, kein backoff — Caller entscheidet

---

## 12. Default Policy

### 12.1 Ohne Guard-Config (Zero-Config)

```
Trust minimum:     verified
Network:           prompt
Filesystem:        prompt
Code execution:    sandboxed
Guard actions:     prompt für delete, write_external, execute, credential_use, unknown
                   allow für read, compute, write_local, network_egress
                   credential_use → allow nur bei deklariertem Connector-Scope
Rate limits:       60/min, 1000/hour
MCP inspection:    ON (path traversal, absolute escape, oversized, URL anomaly → deny;
                       shell tokens, SQL markers → prompt; prompt injection → audit)
Audit:             ON
```

### 12.2 Mit AGENTNODE_GUARD_STRICT=true

```
Trust minimum:     verified (unverändert)
Network:           deny
Filesystem:        deny
Code execution:    deny
Guard actions:     deny für delete, write_external, execute, unknown
                   prompt für write_local, credential_use
                   allow für read, compute
Rate limits:       30/min, 500/hour
MCP inspection:    ON + strenger (shell tokens → prompt, SQL markers → deny)
Audit:             ON
```

Note: Even in strict mode, shell injection markers remain `prompt` (not deny). Heuristic-based detection has false-positive risk and should not silently break legitimate tools.

### 12.3 Agent-spezifisch

```
Agent trust minimum:      trusted (unverändert, härter als toolpack)
Agent Guard:              nur pre_approved_actions aus Manifest/Config erlaubt
                          nicht deklarierte high-risk Actions → deny (non-interactive)
                          critical Actions → deny (immer, auch bei pre_approved)
Agent rate limits:        120/min, 2000/hour (höher wegen Tool-Loop)
```

---

## 13. Scope Phase 6.1 MVP

### Dateien

| Datei | Änderung |
|-------|---------|
| `sdk/agentnode_sdk/guard.py` | NEU — `check_action()`, `inspect_mcp_args()`, `check_rate_limit()`, Action-Klassifikation |
| `sdk/agentnode_sdk/runner.py` | Guard-Integration nach `check_risk_policies()`, vor dispatch |
| `sdk/agentnode_sdk/runtimes/mcp_runner.py` | `inspect_mcp_args()` Call vor `call_tool()` |
| `sdk/agentnode_sdk/policy.py` | Neues Audit-Event `guard_check`, erweiterte Felder |
| `sdk/agentnode_sdk/config.py` | `guard` Section in DEFAULTS und VALID_VALUES |
| `sdk/tests/test_guard.py` | NEU — Tests für allow, deny, prompt, rate limit, MCP inspection |

### MVP Features

1. `guard.check_action()` — Action-Typ-Klassifikation + Policy-Entscheidung
2. `guard.inspect_mcp_args()` — Path Traversal, Absolute Escape, Size, URL-Anomalie, Shell Tokens (prompt)
3. `guard.check_rate_limit()` — In-Memory Sliding Window
4. Integration in `runner.py` (nach `check_risk_policies`)
5. Integration in `mcp_runner.run_mcp()` (vor `call_tool`)
6. Erweiterte Audit-Events
7. `confirmation_callback` Parameter in `run_tool()`
8. Default Guard Policy (Zero-Config)
9. `AGENTNODE_GUARD_STRICT` Modus

### MVP-Tests

- `test_guard_allow_read_action`
- `test_guard_prompt_delete_action`
- `test_guard_deny_critical_risk`
- `test_guard_rate_limit_exceeded`
- `test_guard_mcp_path_traversal_denied`
- `test_guard_mcp_oversized_denied`
- `test_guard_mcp_shell_tokens_prompt_not_deny`
- `test_guard_action_classification_from_manifest`
- `test_guard_action_classification_from_name`
- `test_guard_callback_approve`
- `test_guard_callback_reject`
- `test_guard_strict_mode`
- `test_guard_agent_pre_approved_allow`
- `test_guard_agent_undeclared_high_risk_denied`
- `test_guard_agent_critical_always_denied`
- `test_guard_credential_use_with_connector_allow`
- `test_guard_credential_use_without_connector_prompt`
- `test_guard_audit_event_written`

---

## 14. Explizit NICHT im MVP

| Feature | Grund | Wann |
|---------|-------|------|
| SQL Injection Detection | Zu viele False Positives, braucht Schema-Awareness | Phase 6.2 |
| Prompt Injection in MCP Args | Bereits im agent_runner via `mark_untrusted_tool_output()`, nicht in MCP allgemein | Phase 6.2 |
| Per-Tool Policy (statt per-Package) | Config-Komplexität, reicht per-Package für MVP | Phase 6.2 |
| Install-Time Risk Preview | Nice-to-have, Install ist bereits gut abgedeckt | Phase 6.2 |
| CLI Confirmation UI | SDK liefert Callback-API, CLI-UI ist separater Track | Phase 6.2 |
| Schema-Validation für MCP Args | Braucht Schema-Registry, viele MCP-Tools haben kein Schema | Phase 6.3 |
| Distributed Rate Limiting | In-Memory reicht für Single-Process SDK | Phase 7 |
| Guard Dashboard / Analytics | Audit-Daten sind da, Dashboard ist Web-Track | Phase 7 |
| Real-time Anomaly Detection | Braucht Baseline-Daten die noch nicht existieren | Phase 7+ |

---

## 15. Offene Architekturentscheidungen

### AD-1: Wo lebt guard.py?

- **Option A**: `sdk/agentnode_sdk/guard.py` — eigenes Modul, importiert von `runner.py` und `mcp_runner.py`
- **Option B**: Guard-Logic in `policy.py` erweitern

**Empfehlung: Option A.** `policy.py` ist bereits 691 Zeilen. Guard ist ein neues Konzept mit eigener Config-Section. Saubere Trennung. `policy.py` bleibt Trust/Permission-Layer, `guard.py` wird Action/Argument-Layer.

### AD-2: Name-Heuristik — wie aggressiv?

Wenn ein Tool `delete-user` heißt aber im Manifest `action_type: read` deklariert:

- **Option A**: Manifest gewinnt immer (Publisher wird vertraut)
- **Option B**: Heuristik überstimmt bei Widerspruch (Defense-in-Depth)
- **Option C**: Warnung + höchste Kategorie nehmen

**Empfehlung: Option A für verified+ Trust, Option C für unverified.** Begründung: Verified Publisher hat Incentive korrekt zu deklarieren. Unverified nicht.

### AD-3: Rate Limit Scope

- **Option A**: Per `(slug, tool_name)` — feingranular
- **Option B**: Per `slug` — einfacher, ein Tool pro Package reicht meistens
- **Option C**: Global — ein Limit für alle Tools zusammen

**Empfehlung: Option B für MVP.** Einfach, deckt den häufigsten Fall ab. Per-Tool kann in 6.2 ergänzt werden.

### AD-4: Confirmation Callback — sync oder async?

- **Option A**: Synchroner Callback `Callable[[GuardDecision], bool]`
- **Option B**: Async Callback `Callable[[GuardDecision], Awaitable[bool]]`

**Empfehlung: Option A für MVP.** `run_tool()` ist heute synchron. Async kann später als `arun_tool()` dazukommen.

### AD-5: MCP Argument Inspection — wo genau?

- **Option A**: In `run_mcp()` vor `server.call_tool()` — MCP-spezifisch
- **Option B**: In `runner.py` vor dem dispatch — universell für alle Runtimes
- **Option C**: Beides — generisch in `runner.py`, MCP-spezifisch zusätzlich in `mcp_runner.py`

**Empfehlung: Option C.** `runner.py` bekommt den generischen `check_action()`. `mcp_runner.py` bekommt zusätzlich `inspect_mcp_args()` für MCP-spezifische Patterns (Shell Injection, Prompt Injection), weil MCP-Arguments untypisiertes JSON sind und mehr Angriffsfläche haben als typed Python kwargs.

### AD-6: input_guard.py — upgraden oder ersetzen?

- **Option A**: `input_guard.py` bleibt warning-only, Guard ist die neue Enforcement-Schicht
- **Option B**: `input_guard.py` wird in Guard integriert und kann jetzt blocken

**Empfehlung: Option A für MVP.** `input_guard` bleibt als Defense-in-Depth Warning-Layer. Guard ist die Policy-Schicht die tatsächlich blockt. Kein Breaking Change für bestehende Caller.

### AD-7: Wie werden Guard-Entscheidungen für Agents gehandhabt?

Agent-Tool-Calls gehen durch `AgentContext.run_tool()` → `runner.run_tool()`. Das heißt Guard greift automatisch. Aber Agents können nicht interaktiv prompten.

**Empfehlung: pre_approved_actions Modell.**

- Agents dürfen nur `action_types` ausführen, die in ihrer **Allowlist** oder in **`pre_approved_actions`** (aus Manifest oder Config) deklariert sind.
- Nicht deklarierte high-risk Actions führen zu `prompt`. Da Agents non-interactive sind, wird `prompt` zu **deny** aufgelöst.
- `critical` Actions (risk score >70) werden **immer denied**, auch wenn sie in `pre_approved_actions` stehen.

Begründung: Pauschales implizites Allow für trusted Agents wäre zu breit. Agents brauchen explizite Deklaration welche Action-Typen sie ausführen dürfen. Trust allein reicht nicht — ein Agent mit `trusted` Trust der undokumentiert `delete` Actions ausführt ist ein Risiko. Die Kombination aus Trust-Gate + Allowlist + pre_approved_actions gibt dem Publisher und dem User volle Kontrolle.

---

## 16. Architektur-Invarianten

Ergänzende Invarianten, die vor Phase 6.1 Implementierung gelten.

### INV-1: action_type ist immutable nach Publish

`action_type` bestimmt Risk Score, Guard Decisions, Prompt/Allow-Logik und Audit-Semantik. Deshalb:

- `action_type` darf nach Publish einer Version **nicht mehr verändert** werden.
- Änderung erfordert eine **neue Package-Version**.

Ohne diese Invariante kann ein Publisher still von `action_type: read` auf `action_type: delete` wechseln, ohne semantische Versionsgrenze. Enforcement gehört in die Backend/Registry-Validation (publish endpoint), Spec dokumentiert die Anforderung.

### INV-2: unknown escaliert bei Permission-Signalen

`unknown = medium` ist im MVP akzeptabel als Baseline. Aber ein nicht klassifizierbares Tool mit aktiven Permission-Signalen ist faktisch high-risk. Escalation-Regeln:

```
unknown + network_level != none     → mindestens high
unknown + credential_use            → mindestens high
unknown + code_execution != none    → mindestens high
```

Ohne diese Escalation reicht ein absichtlich obfuskierter Toolname, um Risk künstlich niedrig zu halten.

### INV-3: Rate Limit Memory Bounds

In-Memory Sliding Window (`dict[slug] → list[timestamp]`) braucht Bounds:

- **Stale Entry Cleanup**: Entries älter als `window_size` werden bei jedem Check entfernt.
- **Max Tracked Keys**: Maximal 10.000 aktive Slugs. Ältester Eintrag wird evicted bei Überlauf.
- **Monotonic Clock**: `time.monotonic()` statt `time.time()` — Wall Clock kann springen (NTP, DST, Suspend/Resume).

### INV-4: MCP Argument Inspection Recursion Limits

`inspect_mcp_args()` verarbeitet beliebiges JSON. Ohne Limits wird Inspection selbst zum Angriffsvektor. Definierte Bounds:

- **Max Nesting Depth**: 20 Ebenen. Tiefere Strukturen → deny.
- **Max String Length**: 1MB pro Einzelstring. Längere Strings → deny.
- **Max Total Keys**: 10.000 Keys über die gesamte Struktur. Mehr → deny.

Diese Limits gelten zusätzlich zum Oversized-Check (>1MB Gesamt-Payload).

### INV-5: Confirmation Callback bekommt GuardContext

Aktuelles Interface `Callable[[GuardDecision], bool]` reicht für MVP, aber ein UI braucht Kontext für sinnvolle Darstellung. Erweitertes Interface:

```python
@dataclass
class GuardContext:
    slug: str
    tool_name: str | None
    args_preview: dict        # truncated/redacted kwargs
    request_id: str | None
    trust_level: str

# MVP: GuardDecision only (backward-compatible)
# Post-MVP: Callable[[GuardDecision, GuardContext], bool]
```

MVP implementiert `GuardContext` intern, übergibt es aber noch nicht an den Callback. Post-MVP erweitert die Signatur. Damit ist die Datenstruktur von Anfang an da, ohne Breaking Change beim Callback-Interface.

### INV-6: Audit Decision Lineage

Jeder Audit-Record bekommt ein `guard_chain` Feld, das die vollständige Entscheidungskette dokumentiert:

```json
{
    "guard_chain": [
        "check_run:allow",
        "risk_policy:allow",
        "guard_action:prompt",
        "user_confirmed:allow"
    ]
}
```

Damit ist bei späterer Forensik sofort sichtbar, welche Schicht welche Entscheidung getroffen hat — ohne Audit-Logs korrelieren zu müssen.

### INV-7: Guard ist Pre-Execution Policy, keine Behavioral Sandbox

**Explizite Scope-Limitation**: Guard prüft Inputs vor Execution. Guard sieht nicht, was ein MCP-Server oder Python-Subprocess tatsächlich tut.

```
inspect_mcp_args(args)  → allow
server.call_tool(args)  → Server kann intern beliebige Actions ausführen
```

Guard ist eine **pre-execution policy layer**, keine behavioral sandbox. Runtime-Isolation (Container, Subprocess-Sandboxing) ist ein separater Track und wird nicht durch Guard ersetzt.

Diese Limitation muss in User-facing Dokumentation klar kommuniziert werden, damit Nutzer die tatsächliche Isolationsgrenze nicht überschätzen.
