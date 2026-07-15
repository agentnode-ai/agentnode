# AgentNode — Security Hardening Plan

**Version:** 1.1
**Status:** v1.0 abgestimmt 2026-07-15; **v1.1 = Faktenkorrektur nach unabhängiger Code-Review** (Struktur/Reihenfolge/Zwei-Gate unverändert) · **Reine Planung · Nur Dokumentation · Kein Produktionscode · Kein Deploy**
**Geltung:** SDK-Ausführungsebene (lokale Ausführung auf der Nutzermaschine) + Registry-Vertrauenswurzel. Nicht: Web-App-UI, Deploy-Prozess.

Dieses Dokument ist die kanonische, versionierte Fassung des Sicherheits-Härtungsplans. Es ersetzt vorherige Gesprächsnotizen. Die anschließende unabhängige Review-Runde erfolgt gegen **genau diesen Stand**.

---

## 0. Grundregeln

### 0.1 Zwei-Gate-Modell (die zentrale Regel)

Jeder Slice hat **zwei getrennte Gates**. Deklarierte Sicherheit zählt **nie** allein als „done".

- **① Declaration-Gate** — Die gewünschte Policy ist korrekt modelliert (Manifest-/Config-/Datenmodell, kanonische Form, Fehlersemantik).
- **② Enforcement-Gate** — Durch **negative und Bypass-Tests** ist bewiesen, dass die Policy an **jedem** relevanten Runtime-Pfad tatsächlich greift.

Ein Slice gilt erst als abgeschlossen, wenn ② grün ist. Für MCP-Egress, Sandbox-Routing und Agent-Orchestrierung ist ② wichtiger als jedes weitere Manifestfeld.

### 0.2 Geltungsbereich & Nicht-Ziele dieses Dokuments

- **Ist:** priorisierter, code-verankerter Plan mit belegten Codepfaden und bestehenden Tests je Slice.
- **Nicht-Ziel:** Implementierung. Dieses Dokument ändert keinen Produktionscode und löst keinen Deploy aus. Jeder Slice wird später **einzeln** und **explizit** freigegeben.

### 0.3 Baseline — bereits ausgeliefert (NICHT neu bauen)

Belegt im Code; hier nur als Ausgangslage, damit kein Scope doppelt geplant wird:

| Vorhanden | Beleg |
|---|---|
| Community-Code fail-closed containerisiert **oder** verweigert; kein stiller Host-Fallback | `sdk/agentnode_sdk/sandbox/backend.py:75-97`, `sandbox/container_backend.py:59-60,91,159-183` |
| MCP-Preinstall `network="none"`, env leer, keine Mounts/Secrets | `sandbox/mcp_preinstall.py:156-164` |
| Publisher-Ed25519-Signatur: ungültig blockiert, fehlend warnt | `signature.py`, `installer.py`; `THREAT_MODEL.md:39` |
| Registry-Response-Signing (TG-4) **aktiv**: ≥1 Registry-Key gepinnt (`registry-2026`), Client-Enforcement aktiv; Backend signiert 3 trust-kritische GETs | `registry_trust.py:49-55,97-104`, `client.py`, `key_status.py`, Backend `app/shared/signing_middleware.py:27-31` |
| Per-Entry-Lockfile-Integrität inkl. Signatur-Bindung | `lock_integrity.py` (`CANONICAL_VERSION=3`, `compute_integrity`, `seal_entry`, `verify_entry`) |
| Pre-Execution-Policy-Gate + Guard (Aktionsklassen, kritisches Risiko fail-closed) | `policy.py` (`check_run`), `guard.py` (`check_action`) |
| Agent-Safety-Bounds vorhanden & erzwungen | `runtimes/agent_runner.py` (`_check_tool_limit`, `AgentLimitExceeded`, `max_tool_calls`/`max_iterations`/`max_runtime_seconds`) |
| Community-Agents sandbox-by-default (0.21.0): community→`run_agent_sandboxed` (Container, fail-closed) oder refuse; trusted/curated→Host | `config.py:51` (default `True`); `runtimes/agent_sandbox.py` (`run_agent_sandboxed`, `AgentRpcHost`); `runtimes/agent_runner.py:1361-1382`; Tests `test_agent_sandbox_routing.py` |

**Wichtige Ist-Grenzen (die dieser Plan adressiert):**
- Per-Entry-`_integrity` ist **unkeyed SHA-256** (`lock_integrity.py:175`) → Anti-Fälschungs-Wurzel ist die Signaturebene, nicht der Hash.
- `trust_level` wird beim TTL-Refresh **unsigniert überschrieben** (`runner.py:21,55` `_maybe_refresh_trust`); nicht in den kanonischen Integritätsfeldern → lokale Manipulation nicht erkannt (`THREAT_MODEL.md:60`).
- MCP-Egress-Allowlist ist **deklariert/versiegelt, aber nicht enforced** (`runtimes/mcp_consent.py:108-115`: gültige Domains → weiterhin refuse „pending Stage 4/5").
- `trusted`/`curated` laufen per Default auf dem Host ohne OS-Enforcement (`config.py:54` `host_trust_policy="default"`); der **eigene** Orchestrator-Code eines `trusted`/`curated`-Agents läuft auf dem Host (thread/process), belegt in der SECURITY-INVARIANT-Note `runtimes/agent_runner.py:1338-1346` — nur `trust>=trusted` hält community fern; test-verankert (`test_agent_runner.py:329-336`).

### 0.4 Abhängigkeitsgraph (hart)

```
Track 0  (unabhängig, sofort)
Track 1  (Enforcement-Beweise, unabhängig)
Track 2  (streng sequenziert: Key-Infra → Response-Signing → Rotation/Revocation
          → Trust-Attestation → lokale Bindung → Warn/Kompat → Enforcement)
Track 3  (nach Track-2-Key-Infra)
Track 4  (nach stabiler Registry + Trust)

Slice 0.2B  hängt an  Track 2  (signierte Wurzel)
```

### 0.5 Empfohlene Reihenfolge

`0.1 + 0.2A` → `1.2 + 1.3A` → `1.1` → `0.3` → `Track 2` → `0.2B` → `Track 3` → `Track 4`.

---

## Track 0 — Wahrheits- & Integritätskorrekturen (billig, sofort, unabhängig)

### Slice 0.1 — THREAT_MODEL.md auf Ist-Stand bringen

- **Ist (Code):** `sdk/THREAT_MODEL.md` datiert 2026-05-22; „Future work" listet Container-Sandbox/Netz-Namespace als offen, obwohl `container_backend.py` + `host_trust_policy` (0.21.0) das teils liefern. **Zusätzlich stale sicherheitsrelevante Docstrings/Kommentare, die dem Live-Pfad widersprechen:** `sandbox/agent_rpc.py:1-11` behauptet „un-wired / run_agent unchanged / never invoked", obwohl `agent_sandbox.py` `AgentRpcHost` produktiv verdrahtet (explizit bestätigt in `test_agent_rpc.py:166-186` „now legitimately wired"); `agent_runner.py:1350` behauptet „Flag OFF (default)", obwohl der Default ON ist (`config.py:51`).
- **Bedrohung:** Das Dokument **unterverkauft** die reale Posture; die „does NOT enforce"-Tabelle ist teils überholt (Host-Pfad) bzw. unvollständig (Sandbox-Pfad); stale Docstrings verleiten Auditoren zu falschen „un-wired"-Annahmen → Fehlentscheidungen.
- **Invariante:** Das ausgelieferte Dokument **und** die genannten Docstrings/Kommentare beschreiben exakt den ausgelieferten Enforcement-Stand, getrennt nach **Host-Pfad** und **Sandbox-Pfad**.
- **Scope / Non-Goals:** nur Doku/Kommentare. Umfasst `THREAT_MODEL.md` **und** die stale Docstrings/Kommentare (`agent_rpc.py:1-11`, `agent_runner.py:1350`), besonders wo sie dem Live-Pfad widersprechen. Keine Code-/Policy-Änderung.
- **① Declaration-Gate:** jede Tabellenzeile referenziert eine belegte Funktion (`file:func`).
- **② Enforcement-Gate:** entfällt (Doku); stattdessen Review-Gate: kein Feature erwähnt, das nicht im Code existiert; jede „enforced"-Behauptung hat einen Beleg.
- **Migration/Kompat:** entfällt.
- **Rollout/Rückfall:** reiner Doc-PR; Rückfall = revert.

### Slice 0.2 — Globale Lockfile-Integrität (aufgeteilt in 0.2A + 0.2B)

**Kontext:** Die bestehende per-Entry-`_integrity` ist selbst unkeyed SHA-256 (`lock_integrity.py:175`). Ein unkeyed Top-Level-Hash ist daher konsistent mit dem heutigen Modell (Semantik „erkennt Änderung, sofern nicht bewusst neu versiegelt"), schützt aber **nicht** gegen einen aktiven Manipulator, der das Lockfile ändert und anschließend neu versiegelt. Echter Schutz braucht die signierte Wurzel aus Track 2. Deshalb **zwei Slices**.

#### Slice 0.2A — Struktur-Digest (Track 0, jetzt, unkeyed)

- **Ist (Code):** Integrität nur per Entry (`lock_integrity.py`; `THREAT_MODEL.md:59`: „per-entry, not global. Adding a new malicious entry is not detected"). Lockfile-Schreiben `installer.py:578 update_lockfile`; CLI `lock seal/verify` (`cli/commands.py:3034`).
- **Bedrohung:** versehentliche Beschädigung, partielle Handedits, Tool-Bugs; sowie das **Hinzufügen/Entfernen** eines Entries, das per-Entry unentdeckt bliebe.
- **Invariante:** Jede Änderung der kanonischen **Entry-Menge** ist erkennbar, sofern nicht bewusst neu versiegelt wurde (dann im Review-Diff sichtbar).
- **Naming (ausdrücklich):** Der Top-Level-Wert heißt bewusst **nicht** `*_signature`/`integrity_signature`, sondern z. B. **`structure_digest`** (Alternativen: `lock_structure_hash`, `content_set_digest`). Er ist **keine** Signatur.
- **Kanonische Bildung (ordnungsunabhängig):** über `{lockfile_schema_version, canonicalization_version, je Entry: package_slug + _integrity}` → Entries nach `package_slug` sortieren → pro Entry kanonisieren → Gesamtsatz hashen. Physische Reihenfolge ist bewusst **irrelevant** (Entries sind slug-keyed; Reihenfolge = Präsentation, nicht sicherheitsrelevant).
- **`canonicalization_version` verpflichtend:** fester, nicht optionaler Bestandteil der Digest-Semantik. Zweck: spätere Feld-/Attestation-Änderungen (z. B. Track-2-Attestation-Referenzen am Entry, geänderte `_integrity`-Kanonik) dürfen **nicht still** dieselbe Kanonisierung vortäuschen — jede Änderung der Kanonisierungsregeln erhöht die Version und invalidiert Alt-Digests bewusst.
- **Scope / Non-Goals:** ein deterministischer Struktur-Digest als Top-Level-Feld; `lock seal` schreibt ihn, `lock verify [--strict]` prüft ihn. **Non-Goals:** kein Signaturschema, keine Entry-Format-Änderung, **keine** Reorder-Erkennung (bewusst nicht Teil des Modells), **kein** maschinenlokaler HMAC (bräche das committed-Lockfile/CI-Modell).
- **① Declaration-Gate:** Feld + kanonische Bildung definiert; deterministische Sortierung fixiert; `lock seal` schreibt, `lock verify [--strict]` liest.
- **② Enforcement-Gate (negativ/bypass):**
  - Entry hinzufügen ohne Reseal → Fehler
  - Entry entfernen ohne Reseal → Fehler
  - Entry-Inhalt ändern ohne neues `_integrity` → Fehler
  - vollständigen gültigen Entry aus einem anderen Lockfile einsetzen → Fehler
  - **Reihenfolge ändern → bleibt gültig** (ordnungsunabhängig)
  - Änderung **plus** bewusstes Reseal → technisch gültig, aber im Diff sichtbar
  - altes Lockfile ohne Struktur-Digest → zunächst Warnung, im Strict-Modus später Fehler
  - `--strict` denied vor `run_tool`
  - Erweiterung von `sdk/tests/test_lock_integrity.py` / `test_lock_runtime.py`.
- **Doku-Pflicht:** Der Struktur-Digest ist **keine Signatur** und schützt **nicht** gegen einen Angreifer, der das Lockfile verändern und anschließend neu versiegeln kann. Er erkennt Korruption, Drift und Nicht-Reseal.
- **Migration/Kompat:** v1/v2-Lockfiles ohne Struktur-Digest → Warnung (nicht block), bis `lock seal` neu läuft; CI kann `--strict` verlangen.
- **Rollout/Rückfall:** default warn, opt-in strict; Rückfall = Feature-Flag aus.

#### Slice 0.2B — Signierte globale Lock-Attestation (nach Track-2-Wurzel)

- **Ist:** existiert nicht. Voraussetzung = Track-2-Signaturwurzel.
- **Bedrohung:** aktiver lokaler Manipulator, der Entries **entfernt/ersetzt** und neu versiegelt; sowie die Frage, **welche Entry-Menge für ein konkretes Projekt autorisiert** ist. Per-Entry-Publisher-Signaturen reichen dafür nicht: sie authentifizieren einzelne Einträge, beweisen aber weder, dass kein gültig signierter Eintrag **entfernt** wurde, noch **welche** Menge autorisiert ist.
- **Invariante:** Die autorisierte Gesamtmenge der Pakete ist kryptografisch an das Projekt gebunden und gegen aktive Manipulation geschützt.
- **Bindungsinhalt (mindestens):** `lockfile_digest` (kanonischer Gesamtzustand **inkl. aller `artifact_digest` + Trust-Attestation-Referenzen**), `canonicalization_version`, `project/context identifier`, `issued_at`, `expires_at` bzw. `policy_epoch`, `signing_key_id`, `attestation_version`.
- **OFFENE DESIGNENTSCHEIDUNG (in 0.2B zu klären):** die **Autorität** der Attestation. Die Registry darf das lokale Lockfile **nicht** automatisch signieren — sie kennt die autorisierte Dependency-Auswahl eines konkreten Projekts nicht. Kandidaten: **Projekt-/Organisationsschlüssel**, **CI-Release-Schlüssel**, **Repository-Signing-Identität**, oder eine **externe Deployment-Policy-Attestation**. Diese Wahl bestimmt Rollout und Werkzeuge.
- **Dreier-Kette (Ziel):** Publisher-Signatur (authentifiziert Artefakt) → Trust-Attestation (bindet `trust_level` an `package_version` + `artifact_digest`, Track 2) → **Global Lock-Attestation** (bindet autorisierte Paketmenge ans Projekt, 0.2B).
- **① Declaration-Gate:** kanonisches Attestations-Schema + gewählte Autorität + Replay-Regeln.
- **② Enforcement-Gate (negativ):** entfernter signierter Entry → erkannt; ersetzte Entry-Menge → erkannt; abgelaufene/kontextfremde Attestation → abgelehnt; Attestation eines nicht autorisierten Signers → abgelehnt.
- **Migration/Kompat:** warn → strict, nach Track-2-Reife; nicht vor 2.x.
- **Rollout/Rückfall:** hängt an Track 2; eigenes Gate.

### Slice 0.3 — `load_tool()`/`direct` als Unsafe-Dev-Pfad härten (NIEDRIGE Priorität)

- **Ist (Code):** `client.py:956` + `installer.py:1316` `load_tool()` emittieren bereits `RuntimeWarning("load_tool() bypasses policy checks. Use run_tool() for safe execution.")` (`client.py:968`, `installer.py:1330`). `run_tool` (`runner.py:84`) geht durch `check_run` (`runner.py:166`). `mode="direct"` ist laut `THREAT_MODEL.md` bereits **explizites Opt-in** (default `auto`→subprocess).
- **Bedrohung (eingeordnet):** **Aufrufer-Footgun**, kein paketgetriebener Sandbox-Escape — ein Community-Paket kann `load_tool` nicht gegen den Host auslösen. Daher **niedrige** Kritikalität; gehört **nicht** auf dieselbe Stufe wie manipulierbare Trust-Daten, Lockfile-Integrität oder unbelegtes MCP-Egress-Enforcement.
- **Invariante:** sichere Ausführung ist Standard; `direct` nur mit explizitem Opt-in; unsichere Nutzung ist statisch erkennbar.
- **Scope / Non-Goals:** Warnung behalten/schärfen; optionaler Lint/CI-Check, der `load_tool(`/`mode="direct"` in Nicht-Dev-Code flaggt; Doku trennt Dev vs. Prod. **Non-Goal:** `load_tool` NICHT entfernen (legitimer Dev-Pfad). Präzisierung: `direct` ist bereits Opt-in → die brechende Fläche ist v. a. `load_tool()`-Deprecation + Lint-Verschärfung.
- **① Declaration-Gate:** Doku-Abschnitt „Unsafe developer paths"; Lint-Regel definiert.
- **② Enforcement-Gate:** Test, dass `run_tool` ohne Opt-in nie in `direct` auflöst; Lint-Regel-Test (positiv+negativ); Warnung wird nachweislich emittiert.
- **Migration/Kompat (Kompatphasen):**
  - **Phase A:** bestehendes Verhalten + `RuntimeWarning` + Doku.
  - **Phase B:** neue sichere API-Defaults; bestehende explizite `direct`-Aufrufe funktionieren weiter.
  - **Phase C:** deprecate implizite `direct`-Auflösung; CI-/Lint-Warnung.
  - **Erst Major-Release:** explizites unsafe/`direct`-Opt-in zwingend.
- **Rollout/Rückfall:** additiv; Lint erst „warn", später CI-fail; respektiert die CLI/SDK-Stabilitätsregel (nur additiv/nicht brechend außerhalb Major).

---

## Track 1 — Enforcement-Beweise (vor neuen Kontrollflächen)

### Slice 1.1 — MCP-Egress-Allowlist: Stage-0-Enforcement-Beweis

- **Ist (Code):** Egress **deklariert + versiegelt** (`runtimes/mcp_consent.py` `ConsentIdentity.allowed_domains`, `_norm_domains`; Lockfile `mcp_allowed_domains`; `cli/mcp_commands.py` zeigt `egress`). Ein **Egress-Proxy-Pfad existiert bereits** (`runtimes/mcp_runner.py:89-98` `_credentialed_launch`, Stage 3B-2b, mit Teardown + fail-closed `CredentialedMcpRefused`), ist aber **hinter dem refuse-only Consent gegated** (`mcp_consent.py:108-115 refusal_reason`: auch mit gültigen Domains → refusal „pending Stage 4/5"). Nicht-preinstalled community MCP → refused, kein offenes Netz (`mcp_runner.py:106-117`). Leere Allowlist = hard deny (`REASON_NO_DOMAINS`).
- **Bedrohung:** Der Egress-Proxy-Code existiert, aber seine **Confinement-Wirkung ist empirisch unbewiesen**. Sobald credentialed MCP-Egress freigeschaltet wird, ist die Allowlist wertlos, wenn DNS-Rebinding, Redirects, IP-Literale, Wildcard-Subdomains oder parallele Requests sie umgehen. `proxy-env ≠ enforcement`.
- **Invariante:** Kein Egress außerhalb der versiegelten Domains — **empirisch auf Netzwerkebene**, nicht per Env-Var.
- **Scope / Non-Goals:** **zuerst der Enforcement-Beweis** (Design-A internal-net + dual-homed Proxy als reviewter, syntaxgeprüfter Spike), **bevor** Stage 4/5 gebaut/freigeschaltet wird. **Non-Goals:** kein Freischalten von credentialed Egress in diesem Slice; keine neuen Manifestfelder.
- **① Declaration-Gate:** Domain-Kanonisierung (lowercase, kein trailing dot, sortiert/dedupe — vorhanden in `_norm_domains`); leere Allowlist = deny (vorhanden).
- **② Enforcement-Gate (der Kern, negativ):** Proxy blockt bei DNS-Rebinding, 3xx-Redirect auf Fremd-Domain, IP-Literal statt Domain, nicht-allowlisted Subdomain, mehreren sequentiellen/parallelen Requests, direkter Socket-Umgehung des Proxys.
- **Migration/Kompat:** bleibt **refuse-only**, bis ② grün.
- **Rollout/Rückfall:** kein Rollout ohne Beweis; Rückfall = refuse-only.

### Slice 1.2 — „Kein stiller Sandbox→Host-Fallback": Beweis-Härtung

- **Ist (Code):** `sandbox/backend.py:75-97` (fail-closed default; RefusingBackend refuses), `container_backend.py:91 check_available` (kein Pull), `159-183` Netzmodi explizit fail-closed, `runner.py:155-157` `enforce_sandbox_policy`/`SandboxRequiredError`. Tests: `test_agent_sandbox_routing.py`, `test_agent_session_container.py`, `test_agent_sandbox_e2e.py`.
- **Bedrohung:** ein Refactor/Edge-Case fällt doch auf Host zurück (Image fehlt, Runtime weg, unbekannter Netzwert).
- **Invariante:** Bei fehlender/unbrauchbarer Isolation läuft Community-Code **nie** auf Host — überall Refuse.
- **Scope / Non-Goals:** reine Test-/Assertion-Härtung aller Backend-Pfade; **keine** Verhaltensänderung; **keine** neue Isolationstechnik.
- **① Declaration-Gate:** Routing-Matrix (trust × runtime-verfügbar × network) dokumentiert.
- **② Enforcement-Gate (negativ):** Image fehlt / Runtime nicht installiert / unbekannter Netzwert / korruptes Image-Digest / Backend wirft mitten im Lauf → jeweils `SandboxRequiredError`/refuse, **kein** Host-Exec; „Fallback-Trap"-Test über jeden Pfad, der `HostBackend` für Community erreichen könnte.
- **Migration/Kompat:** entfällt (nur Tests).
- **Rollout/Rückfall:** additive Tests; kein Prod-Risiko.

### Slice 1.3A — Agent-Enforcement (Ist beweisen)

- **Ist (Code):** Zwei Live-Ausführungskontexte, verdrahtet in `runtimes/agent_runner.py:1361-1382`: (a) **community** (verified/unverified, Flag ON per Default) → `runtimes/agent_sandbox.py` `run_agent_sandboxed` → Container + `AgentRpcHost` (host-seitige Allowlist + `max_tool_calls`-Limit; `agent_sandbox.py:188-198`), sonst fail-closed refuse; (b) **trusted/curated** → Host-Runner (`agent_runner.py`, `AgentContext` erzwingt `max_tool_calls`/`max_iterations` via `_check_tool_limit`/`AgentLimitExceeded`), Routing in Sandbox nur via `host_trust_policy`. Limitwerte aus dem Manifest-`limits`-Block, Defaults 12/40/180 (`agent_runner.py:1385-1388`). *(Die stale `agent_rpc.py:1-11`-Docstring nicht mehr als Ist-Beleg — siehe Slice 0.1.)*
- **Bedrohung:** der **eigene** Orchestrator-Code eines **trusted/curated**-Agents läuft unsandboxed auf Host; Limits/Allowlist müssen an **beiden** Kontexten greifen. Nuance: im Sandbox-Kontext ist `max_tool_calls` host-autoritativ (`AgentRpcHost`), `max_iterations`/`max_runtime` sind container-/Timeout-seitig — das Enforcement-Gate muss beide Ebenen abdecken.
- **Invariante:** deklarierte Tool-Allowlist und Safety-Bounds greifen an **jedem** Ausführungskontext gleich; **Manifestwerte werden vollständig honoriert und nicht durch einen versteckten Plattform-Cap geklemmt** — Defaults sind 12/40/180, ein expliziter Unlimited-/Disable-Sentinel existiert derzeit **nicht** (AgentNode hostet die Agenten nicht; kein Plattform-Ceiling über dem Manifestwert).
- **Scope / Non-Goals:** Kartierung + Enforcement-Tests; ehrliche Doku des Trusted-Host-Vektors. **Bestehendes `AgentLimitExceeded`-Verhalten wird beibehalten — keine Verhaltensänderung.** **Non-Goals:** trusted-Agent-Sandboxing = eigener späterer Bogen; kein Checkpoint/Resume; **keine** neue Ergebnissemantik (siehe geparktes 1.3B).
- **① Declaration-Gate:** `limits`-Schema dokumentiert als konfigurierbare Safety-Bounds (Autor-Manifest, Defaults 12/40/180); es wird **nicht** verlangt, dass ein „unlimited"-Sentinel existiert oder getestet wird.
- **② Enforcement-Gate:** **bereits test-belegt** — Host-Pfad-Limits (`AgentLimitExceeded` für max_tool_calls/max_iterations, Werte 42/7 > Defaults honoriert: `test_agent_runner.py:146-152,200-227`), RPC-Allowlist + Tool-Call-Limit host-seitig (`test_agent_rpc.py:87,94`), community sandbox-or-fail-closed/nie-Host + Spec-Härtung (`test_agent_sandbox_routing.py`), Exec-Vektor-Invariante (`test_agent_runner.py:329-336`). **Noch als geplantes Gate:** prüfen, dass Werte **oberhalb** der Defaults nicht intern geklemmt werden (nicht: ein „unlimited"-Sentinel testen); `max_iterations`/`max_runtime`-Durchsetzung im Sandbox-Kontext (container-/Timeout-seitig) explizit abdecken.
- **Migration/Kompat:** keine (Verhalten unverändert).
- **Rollout/Rückfall:** Tests + Doku; kein Prod-Risiko.

> **GEPARKT — Slice 1.3B (NICHT Bestandteil des aktuellen Umsetzungsumfangs).**
> Strukturiertes Abbruchergebnis (`status: safety_limit_reached`, `task_complete: false`, `checkpoint_available`), API-Kompatibilität, CLI-Darstellung, Telemetrie, ggf. Resume-Konzept. Dies ist eine **API-/Laufzeitänderung** gegenüber dem heutigen `AgentLimitExceeded` und wird **erst bei nachgewiesenem Nutzerbedarf** geplant. Bis dahin: nicht im Scope.

---

## Track 2 — Trust-Level Signing Arc (streng sequenziert)

**Wichtig — zwei getrennte Sicherheitsobjekte:**
1. **Registry-Response-Authentizität** (TG-4): beweist, dass eine Antwort von der Registry stammt und nicht downgraded/manipuliert wurde. Ephemer, pro Request.
2. **Trust-Attestation:** eine dauerhafte, eigenständige signierte Aussage über Paket, Version, `artifact_digest`, Publisher, `trust_level` für einen definierten Zeitraum.

Eine gültig signierte Registry-**Response** ist **keine** dauerhafte Trust-Attestation. Die Attestation braucht ein **eigenes kanonisches Schema**, Audience/Context-Bindung und Replay-Regeln.

- **Ist (Code):** TG-4 **aktiv** — `registry_trust.py:49-55` pinnt `registry-2026`, `enforcement_active()=True` (`:97-104`). Backend signiert genau **3 trust-kritische GETs** (`/v1/packages/{slug}`, `/…/install-info`, `/v1/publishers/{slug}/keys/{key_id}`) mit Ed25519 über den Response-Body (`app/shared/signing_middleware.py:27-31,101-107`; Key aus `settings.REGISTRY_SIGNING_KEY(_ID)`, `main.py:202-203`; Degraded-Mode = unsigniert). Missing/Invalid → client-seitig deny (Downgrade-Schutz). `key_status.py` (online Revocation, `lock verify --online`), Publisher-Signatur `signature.py`/`signing_key.py`/`cli/publish.py`. **Offene Evidenz — NICHT als vollständige Backend-Abdeckung behaupten:** dass der deploy-konfigurierte Backend-Key tatsächlich `registry-2026` ist und zum client-gepinnten Public Key passt, ist ein Deploy-Fakt (nicht code-verifizierbar). **Replay/Freshness NICHT abgedeckt** (nur Body signiert, kein Timestamp/Nonce; `THREAT_MODEL.md:62` „TG-5+"). **Gap:** `runner.py:21,55 _maybe_refresh_trust` überschreibt `trust_level` unsigniert; nicht in kanonischen Integritätsfeldern (`THREAT_MODEL.md:60`). Tests: `test_signature.py`, `test_signing_key.py`, `test_key_status.py`, Backend `test_signing_middleware.py`.

**Sequenz (je Teilstufe eigenes ①/② und eigenes Gate):**
- **2.1 Registry-Trust-Wurzel härten (KEIN Bootstrap — Key ist gepinnt)** — Restarbeit auf dem aktiven TG-4: **vollständige Endpunktabdeckung** verifizieren/erweitern und **operativen Deploy-Match** zum client-gepinnten Public Key sicherstellen (die offene Evidenz oben schließen). ② negativ: unsignierte/fremdsignierte Response → deny bei aktiver Enforcement (Downgrade).
- **2.2 Response-Signing-Abdeckung + Anti-Replay (NEU)** — Anti-Replay/Freshness ist **noch nicht vorhanden** (nur Body signiert; `THREAT_MODEL.md:62` TG-5+) → als Neubau, nicht als Test: Timestamp/Nonce/Sequence. ② Replay-Beweis (gecapturte gültige Response erneut → abgelehnt) + Downgrade-Beweis.
- **2.3 Key-Rotation, Revocation & Multi-Key** — Multi-Key-Fähigkeit (parallele gültige Keys) + dokumentierter Recovery-Prozess. ② revozierter Key → install+verify block.
- **2.4 Signierte Trust-Attestation** — eigenes Schema. **Muss enthalten:** `package_id`, `package_version`, **`artifact_digest`** (Kern — verhindert Version-Reuse/Artefakt-Swap), `publisher_identity`, `trust_level`, `issued_at`, `expires_at`, `attestation_version`, `registry_key_id`, `context/audience`. ① Schema.
- **2.5 Lokale Bindung** — `trust_level` nur akzeptiert, wenn Attestation-Signatur valide **und** `attestation.artifact_digest` == im Lockfile gespeicherter Artefakt-Hash. `_maybe_refresh_trust` prüft vor Überschreiben. ② negativ: lokale Mutation unverified→trusted ohne gültige Attestation → erkannt/abgelehnt; TTL-Refresh ohne gültige Signatur überschreibt nicht; Attestation über andere Bytes derselben Version → abgelehnt.
- **2.6 Warn-/Kompatphase → Enforcement** — erst Telemetrie/Warn, dann `missing/invalid attestation = deny`, zuletzt Bestandsartefakte.

- **Rollout/Rückfall je Stufe:** TG-4 bleibt im aktuellen **aktiven** Zustand (Key gepinnt) — es gibt keinen „Bootstrap=allow"-Ausgangszustand mehr zurückzurollen; **neue** Enforcement (Attestation/Anti-Replay) zuerst warn, dann strict; jede Stufe einzeln gegatet; Rückfall = neues Enforcement-Flag zurück auf warn.

---

## Track 3 — Installations- & Dependency-Provenienz (nach Track-2-Key-Infra)

- **Ist (Code):** Audit = **nur Policy-Entscheidungen** (`policy.py` `audit_decision`, `~/.agentnode/audit.jsonl`, bewusst ohne Inputs/Outputs). Artefakt-Hash im Lockfile vorhanden; **transitive** Deps via pip im `installer.py`-Pfad **ohne** per-Dep-Hash (`CANONICAL_FIELDS_V3` hat kein `dependencies`). Tests: `test_install_hardening.py`, `test_installer_sprint_b.py`.
- **Bedrohung:** zwei Installs desselben Pakets können abweichende transitive Versionen ziehen; keine forensische „was-wurde-installiert"-Spur.
- **3.1 Datensparsame Installation Receipts** — `run_id`, Artefakt-Hash, Signaturstatus, Policy-Hash, Sandbox-Digest, Lockfile-Hash vorher/nachher; **keine** Secrets/Args-Werte (redigiert). ② negativ: Secret/PII taucht nie in Receipt/Log auf; fehlgeschlagene Installs dokumentiert; Hash-Verkettung erkennt nachträgliche Manipulation.
- **3.2 Transitive Dep-Pins + Artefakt-Hashes** (Lockfile-Erweiterung) — jede aufgelöste Dep mit `sha256`. ② negativ: veränderte transitive Dep → Drift-Fehler; Offline-Install aus Lock+Cache → identische Artefakte.
- **3.3 SBOM (CycloneDX) + Source-Commit→Artefakt-Bindung.** ② SBOM deckt sich mit real installierten Artefakten.
- **Datenschutz-Non-Goal:** Receipts erweitern den Audit-Trail **nicht** um Tool-Inputs/Outputs — sonst neuer Leak-Vektor.

---

## Track 4 — Build-Provenienz (zuletzt; nach stabiler Registry + Trust)

- **Ist (Code):** Publish-Verifikation = install+smoke+test in sauberer Umgebung (funktional, **kein** bit-Repro-Beweis).
- **Sequenz:** 4.1 definierter Builder (Image-Digest) + Build-Env-Digest → 4.2 Provenance-Attestation (in-toto/SLSA-Stil) + Source→Artefakt-Bindung → 4.3 Registry-Rebuild → 4.4 unabhängige Doppel-Builds + bitgenauer Vergleich → Reproducibility-Badge.
- **Non-Goal jetzt:** kein bit-Repro-Zwang, bevor 4.1/4.2 Wert liefern. Großbogen (Registry + Pipeline + Manifest + SDK + CLI + Website).

---

## Offene Designentscheidungen

- **0.2B — Autorität der globalen Lock-Attestation:** Projekt-/Org-Key vs. CI-Release-Key vs. Repo-Signing-Identität vs. externe Deployment-Policy-Attestation. **Keine** Registry-Auto-Signierung. Bestimmt Rollout und Werkzeuge; vor 0.2B-Umsetzung zu entscheiden.
- **0.2A — Feldname:** `structure_digest` (Arbeitstitel) vs. `lock_structure_hash` vs. `content_set_digest`.

## Geparkte Themen (nicht im aktuellen Umsetzungsumfang)

- **1.3B** — strukturierte Limit-Ergebnissemantik / Checkpoint / Resume (nur bei nachgewiesenem Nutzerbedarf).
- **Trusted-Agent-Sandboxing** — eigener späterer P0-Bogen (siehe Agent-Execution-Vector).

## Offene Evidenz & Testabdeckung (Stand v1.1)

**Offene Evidenz (nicht abschließend belegt):**
- **Backend-Signatur-Key-Identität:** dass der deploy-konfigurierte `REGISTRY_SIGNING_KEY_ID` == `registry-2026` ist und der private Key zum client-gepinnten Public Key passt — Deploy-Fakt, im Code-Scope nicht verifizierbar. **Keine** vollständige Backend-Signaturabdeckung behaupten.
- **Anti-Replay:** nicht implementiert (nur Body signiert; TG-5+) → Track 2.2 behandelt es als **Neubau**, nicht als Test.
- **User-seitige Overrides von Agent-Limits:** unklar, ob ein Nutzer die im Manifest deklarierten Limits überschreiben/senken kann (nur Autor-Manifest-Quelle belegt, `agent_runner.py:1385`). **Offen; blockiert v1.1 nicht.**
- **Toolpack-/MCP-„kein Host-Fallback":** der Agent-Pfad ist test-belegt; die äquivalente Abdeckung für Toolpack-/MCP-Container-Fallback wurde hier nicht direkt gelesen (`test_install_hardening.py`, Container-Backend-Tests).

**Bereits durch Tests belegt (keine neuen Tests nötig):** community sandbox-or-fail-closed/nie-Host, Flag-Default-ON, fail-closed bei fehlendem Volume/Backend/Session, Spec-Härtung (network=none/read-only/keine Keys), host-seitige Allowlist- + Tool-Call-Limit-Refusal, Host-Pfad-`AgentLimitExceeded`, Werte über Defaults honoriert, Exec-Vektor-Invariante, `agent_rpc` nur via `agent_sandbox` verdrahtet, Audit-Sanitisierung — `test_agent_sandbox_routing.py`, `test_agent_rpc.py`, `test_agent_runner.py`.

**Erst als geplante negative Enforcement-Gates (noch NICHT test-belegt):** 0.2A Struktur-Digest; 1.1 MCP-Egress-Proxy-Confinement; Track-2 Anti-Replay + Attestation-Bindung; „Werte oberhalb Defaults nicht geklemmt" als dedizierter Test; `max_iterations`/`max_runtime` im Sandbox-Kontext.

## Änderungshistorie dieses Dokuments

- **v1.0 (2026-07-15):** Erstfassung. Enthält die vier abgestimmten Korrekturen (echtes Vertrauensfundament + Sortier/Reorder-Auflösung für den globalen Lock-Wert; 1.3A/1.3B-Trennung; load_tool/direct-Kompatphasen; Trennung Registry-Response-Authentizität ↔ Trust-Attestation) sowie den 0.2A/0.2B-Split inkl. `structure_digest`-Semantik und 0.2B-Autoritätsentscheidung. Reine Planung.
- **v1.1 (2026-07-15):** Faktenkorrektur nach unabhängiger Code-Review (Struktur/Reihenfolge/Zwei-Gate unverändert). **Track 2:** Ausgangslage korrigiert — TG-4 **aktiv**, `registry-2026` gepinnt (kein Bootstrap); 2.1/2.2/2.3 auf reale Restarbeit umgestellt (Endpunktabdeckung, operativer Deploy-Match, Anti-Replay als Neubau, Multi-Key/Rotation/Revocation); Backend-Abdeckung als offene Evidenz markiert. **Slice 1.3A:** auf die realen Live-Pfade (`run_agent_sandboxed`/`AgentRpcHost` + Host-Runner) und die präzise Limit-Invariante („Manifestwerte ohne versteckten Plattform-Cap; Defaults 12/40/180; kein First-Class-Unlimited-Sentinel") umgestellt; stale Docstrings entfernt; Testabdeckung ausgewiesen. **Slice 1.1:** Ist um den bereits existierenden Egress-Proxy-Pfad (Stage 3B-2b, hinter Refuse-only-Consent) ergänzt; Confinement empirisch zu beweisen; Enforcement-Gate unverändert. **Slice 0.1:** um stale Sicherheits-Docstrings (`agent_rpc.py:1-11`, `agent_runner.py:1350`) erweitert. **Slice 0.2A:** `canonicalization_version` als verpflichtend festgehalten. Neue Sektion „Offene Evidenz & Testabdeckung". Reine Planung.
