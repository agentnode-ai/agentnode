# Sprint C Tag 2 — Deep Review + Problemklassen

**Date:** 2026-03-26
**Reviewed:** 12 HIGH+draft_ready cases (all)
**Reviewer:** automated + manual inspection

---

## Review Checklist: HIGH + draft_ready Cases

### 1. lc_calculator.py (LangChain)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `calculate` |
| Helper vollstandig | N/A (no helpers) |
| Konstanten vollstandig | N/A |
| Dependencies korrekt | YES — none (stdlib only) |
| Manifest plausibel | YES — v0.1, single entrypoint |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — complete calculator with error handling |
| Warning/Changes verstandlich | YES |
| **capability_id** | `code_analysis` — WEAK (should be something like `math` but no such capability exists) |
| **categories** | `['code']` — acceptable fallback |
| **Urteil** | **Korrekt high** |

### 2. lc_email_sender.py (LangChain)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `send_email` |
| Helper vollstandig | YES — SMTP constants preserved as top-level |
| Konstanten vollstandig | YES — SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS |
| Dependencies korrekt | YES — none (smtplib is stdlib) |
| Manifest plausibel | YES |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — complete email sending tool |
| Warning/Changes verstandlich | YES — env var warning present |
| **capability_id** | `email_drafting` — CORRECT (matches "email" keyword) |
| **categories** | `['email']` — CORRECT |
| **Urteil** | **Besonders gutes Beispiel** |

### 3. lc_mixed_patterns.py (LangChain)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `ping_url`, `http_request` (2 tools) |
| Helper vollstandig | YES — HTTPRequestInput Pydantic class preserved |
| Konstanten vollstandig | N/A |
| Dependencies korrekt | YES — requests, pydantic |
| Manifest plausibel | YES — v0.2 with per-tool entrypoints |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — both tools complete and functional |
| Warning/Changes verstandlich | YES — args_schema warning, BaseTool extraction noted |
| **capability_id** | first tool: `code_analysis` — WEAK (HTTP tool, not code analysis) |
| **categories** | `['code']` — WEAK (should be `web` or `api`) |
| **Urteil** | **Korrekt high** — category/capability is cosmetic, not blocking |

### 4. lc_pdf_reader.py (LangChain)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `read_pdf` |
| Helper vollstandig | N/A |
| Konstanten vollstandig | N/A |
| Dependencies korrekt | YES — PyPDF2 |
| Manifest plausibel | YES — filesystem permissions correct |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — complete PDF reader with error handling |
| Warning/Changes verstandlich | YES |
| **capability_id** | `pdf_extraction` — CORRECT |
| **categories** | `['pdf']` — CORRECT |
| **Urteil** | **Besonders gutes Beispiel** |

### 5. lc_simple_search.py (LangChain)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `search_web` |
| Helper vollstandig | YES — SERP_API_KEY constant preserved |
| Konstanten vollstandig | YES |
| Dependencies korrekt | YES — requests |
| Manifest plausibel | YES — network: unrestricted |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES |
| Warning/Changes verstandlich | YES |
| **Note** | Hardcoded API key `sk-...` preserved from source — no warning about credentials |
| **capability_id** | `web_search` — CORRECT (matches "search the web") |
| **categories** | `['web']` — CORRECT |
| **Urteil** | **Korrekt high** |

### 6. lc_tool_env_vars.py (LangChain)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `query_notion_database` |
| Helper vollstandig | YES — `_notion_headers()` helper preserved |
| Konstanten vollstandig | YES — OPENAI_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID, NOTION_VERSION |
| Dependencies korrekt | YES — requests |
| Manifest plausibel | YES |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — complete Notion API tool |
| Warning/Changes verstandlich | YES — env var warnings present |
| **capability_id** | `sql_generation` — WRONG (matches "query" keyword, but this is Notion, not SQL) |
| **categories** | `['sql']` — WRONG |
| **Urteil** | **Formal ok, aber capability_id falsch** — das ist kosmetisch, nicht blockierend |

### 7. lc_tool_with_class.py (LangChain)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `get_github_repo_info` |
| Helper vollstandig | YES — `GitHubRepo` dataclass preserved with all methods |
| Konstanten vollstandig | N/A |
| Dependencies korrekt | YES — requests |
| Manifest plausibel | YES |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — tool uses helper class correctly |
| Warning/Changes verstandlich | YES |
| **capability_id** | `code_analysis` — WEAK (GitHub repo info, not code analysis) |
| **categories** | `['code']` — acceptable (it's code-related) |
| **Urteil** | **Korrekt high** |

### 8. lc_two_tools.py (LangChain)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `get_weather`, `get_ip_info` (2 tools) |
| Helper vollstandig | YES — `_make_request()` shared helper preserved |
| Konstanten vollstandig | N/A |
| Dependencies korrekt | YES — requests |
| Manifest plausibel | YES — v0.2 with per-tool entrypoints |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — both tools complete |
| Warning/Changes verstandlich | YES |
| **capability_id** | `code_analysis` — WRONG (weather/geo tools, not code) |
| **categories** | `['code']` — WRONG (should be `web` or `weather`) |
| **Urteil** | **Korrekt high** — capability classification is the only issue |

### 9. lc_web_scraper.py (LangChain)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `scrape_webpage` |
| Helper vollstandig | YES — HEADERS constant preserved |
| Konstanten vollstandig | YES |
| Dependencies korrekt | YES — requests, bs4 |
| Manifest plausibel | YES — network: unrestricted |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — complete scraper with cleanup |
| Warning/Changes verstandlich | YES |
| **capability_id** | `webpage_extraction` — CORRECT |
| **categories** | `['webpage']` — CORRECT |
| **Urteil** | **Besonders gutes Beispiel** |

### 10. cr_api_tool.py (CrewAI)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `weather_api` |
| Helper vollstandig | YES — API constants preserved |
| Konstanten vollstandig | YES — WEATHER_API_KEY, WEATHER_BASE_URL |
| Dependencies korrekt | YES — requests |
| Manifest plausibel | YES |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — complete weather API tool |
| Warning/Changes verstandlich | YES — env var warning |
| **capability_id** | `code_analysis` — WRONG (weather tool) |
| **categories** | `['code']` — WRONG |
| **Urteil** | **Korrekt high** — capability classification only issue |

### 11. cr_simple_tool.py (CrewAI)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `duck_duck_go_search` |
| Helper vollstandig | N/A |
| Konstanten vollstandig | N/A |
| Dependencies korrekt | YES — requests |
| Manifest plausibel | YES |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES |
| Warning/Changes verstandlich | YES |
| **capability_id** | `web_search` — CORRECT (matches "search") |
| **categories** | `['web']` — CORRECT |
| **Urteil** | **Besonders gutes Beispiel** |

### 12. cr_two_tools.py (CrewAI)

| Check | Result |
|-------|--------|
| Tool erkannt | YES — `stock_price_lookup`, `stock_news_fetcher` (2 tools) |
| Helper vollstandig | YES — ALPHA_VANTAGE_KEY constant preserved |
| Konstanten vollstandig | YES |
| Dependencies korrekt | YES — requests |
| Manifest plausibel | YES — v0.2 |
| Output parsebar | YES |
| Draft semantisch brauchbar | YES — both financial tools complete |
| Warning/Changes verstandlich | YES — env var warning |
| **capability_id** | `code_analysis` — WRONG (finance tools) |
| **categories** | `['code']` — WRONG |
| **Urteil** | **Korrekt high** — capability only issue |

---

## Summary: Review Verdicts

| File | Verdict |
|------|---------|
| lc_calculator.py | Korrekt high |
| lc_email_sender.py | Besonders gutes Beispiel |
| lc_mixed_patterns.py | Korrekt high |
| lc_pdf_reader.py | Besonders gutes Beispiel |
| lc_simple_search.py | Korrekt high |
| lc_tool_env_vars.py | Formal ok, capability falsch |
| lc_tool_with_class.py | Korrekt high |
| lc_two_tools.py | Korrekt high |
| lc_web_scraper.py | Besonders gutes Beispiel |
| cr_api_tool.py | Korrekt high |
| cr_simple_tool.py | Besonders gutes Beispiel |
| cr_two_tools.py | Korrekt high |

**Keiner der 12 Falle ist eigentlich eher medium. Alle HIGH-Einstufungen sind korrekt.**

---

## Top 3 Problemklassen

### Problem 1: Schwache capability_id / categories Inferenz (Kosmetisch, Mittel-Prio)

**Haufigkeit:** 6 von 12 HIGH-Falle bekommen `code_analysis` als Fallback
**Betroffene Falle:** calculator, mixed_patterns, tool_with_class, two_tools, cr_api_tool, cr_two_tools
**Falsche Zuordnung:** lc_tool_env_vars bekommt `sql_generation` wegen "query" im Funktionsnamen

**Warum passiert das:**
- `_CAPABILITY_MAP` hat 33 Eintrage, deckt aber viele Domains nicht ab
- "weather", "stock", "finance", "github", "math" sind nicht als Keywords enthalten
- "query" matcht auf `sql_generation`, obwohl es auch Notion/API-Queries sein kann
- Fallback ist `code_analysis` — sichtbar als Placeholder, aber kein User versteht das

**Risiko:** Niedrig — kosmetisch. Das Manifest besteht trotzdem Validierung. Aber es wirkt unpoliert auf den Benutzer.

**Fix-Aufwand:** Klein — Keywords erweitern. Kein Architektur-Change.

**Empfehlung:** Kleine Keyword-Erweiterung in Tag 3:
- `["weather", "forecast", "temperature", "climate"]` -> `web_search` oder neue capability
- `["stock", "finance", "ticker", "quote", "market"]` -> `data_cleaning` oder neues
- `["github", "repo", "repository", "git"]` -> `code_analysis` (passt tatsachlich)
- `["math", "calculate", "expression", "arithmetic"]` -> `code_analysis` (passt auch)
- "query" aus `sql_generation` Keywords entfernen oder nur matchen wenn auch "sql" oder "database" vorkommt

### Problem 2: `langchain_openai` als Unknown Import (Klein, Niedrig-Prio)

**Haufigkeit:** 2 von 30 Corpus-Dateien
**Auswirkung:** Warning "Import nicht zuordenbar", aber da es nicht im Body aktiv benutzt wird -> medium, nicht low

**Warum passiert das:**
- `langchain_openai` ist weder in KNOWN_THIRD_PARTY noch in LANGCHAIN_MODULES
- Es ist ein eigenstandiges PyPI-Package (`pip install langchain-openai`)
- In der Praxis wird es oft neben langchain importiert

**Fix-Aufwand:** 1 Zeile — zu KNOWN_THIRD_PARTY hinzufugen

**Empfehlung:** Ja, in Tag 3 umsetzen. Auch `langchain_community`, `langchain_core` als Third-Party ergaenzen (sind zwar Framework-adjacent, aber eigenstandige PyPI-Packages die ein User explizit installiert).

### Problem 3: Keine Warnung bei hardcodierten Credentials (Nice-to-have, Sprint D)

**Haufigkeit:** 1 von 12 HIGH-Falle (`lc_simple_search.py` hat `SERP_API_KEY = "sk-..."`)
**Auswirkung:** Keine — der Code funktioniert. Aber es ist ein Sicherheits-Hinweis der dem User helfen wurde.

**Warum passiert das nicht:** Kein Pattern-Matching auf String-Literals die wie API-Keys aussehen.

**Fix-Aufwand:** Mittel — Pattern-Matching auf `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD` mit String-Literal-Wert implementieren. False-Positive-Risiko bei kurzen Strings.

**Empfehlung:** Nicht in Tag 3. Fur Sprint D vormerken. Kein Confidence-Impact, nur eine informative Warning.

---

## Zusatzliche Beobachtungen

### Positiv
- **Helper-Preservation funktioniert ausgezeichnet**: Klassen, Funktionen, Konstanten werden korrekt ubernommen
- **Multi-Tool (v0.2) funktioniert fehlerfrei**: 3 von 12 Fallen sind Multi-Tool, alle korrekt
- **Env-Var-Detection ist prazise**: Alle relevanten env vars erkannt, keine false positives
- **Permission-Inferenz ist korrekt**: network/filesystem passend zum Tool-Inhalt
- **Changes-Texte sind verstandlich und vollstandig**
- **Warnings sind ehrlich und actionable**

### Neutral
- `requires_manual_review = False` fur alle 12 HIGH-Falle — korrekt, keine manuelle Nacharbeit nötig
- Package-ID Warning ist immer vorhanden — erwartungsgemaess

---

## Entscheid: Regelanpassungen fur Tag 3

| Anpassung | Entscheid | Begrundung |
|-----------|-----------|------------|
| capability_id Keywords erweitern | JA | 6/12 HIGH mit falschem Fallback — kosmetisch aber sichtbar |
| `langchain_openai` als Third-Party | JA | 2/30 Corpus, triviale Anderung |
| "query" in sql_generation einschranken | JA | Verursacht falsche Zuordnung (Notion -> SQL) |
| Hardcoded Credentials Warning | NEIN | Sprint D — kein Confidence-Impact, hoeherer Aufwand |
| Neue Capabilities hinzufugen | NEIN | Nur existierende CapabilityTaxonomy nutzen |
