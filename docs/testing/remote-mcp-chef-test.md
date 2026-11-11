# #6 Remote-MCP – Chef-Test (live, mit echten Clients)

Zweck: den Remote-MCP-Kernweg (Smoke-Kernweg 2) einmal so prüfen, wie ein Nutzer ihn erlebt –
gegen `mcp.agentnode.net` mit echtem Claude Code und echtem Claude Desktop. Lokal ist der Weg mit
einem Standard-MCP-Client belegt (`sdk/tests/remote_mcp/test_e2e_standard_client.py`, T-0066 bis T-0069);
offen ist nur, was nur live geht.

Regeln: keine persönlichen Werte (Token, Codes, Kontodaten, Mail-Adressen) in Notizen oder
Screenshots – Werte schwärzen, Länge und Form genügen. Jeden Schritt mit Uhrzeit festhalten.
Abbruch bei unerwartetem Verhalten: nicht improvisieren, Zustand notieren.

| # | Schritt | Erwarteter Nachweis |
|---|---|---|
| 1 | **Erreichbarkeit und Discovery.** `https://mcp.agentnode.net/.well-known/oauth-protected-resource` und `.../.well-known/oauth-authorization-server` im Browser öffnen; einen unangemeldeten `POST /mcp` absetzen (z. B. per Client-Verbindungsversuch). | Beide Dokumente laden (HTTP 200). PRM nennt `resource = https://mcp.agentnode.net/mcp` und genau einen Authorization Server. AS-Metadaten: `client_id_metadata_document_supported: true`, `code_challenge_methods_supported: ["S256"]`, ob `registration_endpoint` fehlt (DCR aus) – notieren. Unangemeldet: 401 mit `WWW-Authenticate: Bearer resource_metadata="…"`. |
| 2 | **Claude Code verbinden.** Server als Remote-MCP (HTTP) mit der URL `https://mcp.agentnode.net/mcp` hinzufügen und verbinden. | Notieren: ob Claude Code ein CIMD anbietet (Client-ID ist eine https-URL) oder DCR versucht oder scheitert; welche Meldung erscheint. Im Server-Log/Audit (Betrieb) die Client-Herkunft `cimd` / `preregistered` bestätigen. Kein Wert der Client-ID-URL nötig, nur die Art. |
| 3 | **OAuth/PKCE und Consent** mit dem Chef im Browser durchlaufen (Anmeldung, Consent-Seite, „Allow“). | Browser wird an den lokalen Callback des Clients zurückgeleitet; Claude Code meldet „verbunden“. Consent-Seite nennt den Client und den Zugriff (`mcp:use`). Zeitpunkt der Autorisierung notieren (für Schritt 7). |
| 4 | **Tools und ein sicherer Aufruf.** Tools auflisten; ein Tool ohne Seiteneffekt wählen (z. B. Regex-Builder `findall` auf synthetischem Text); aufrufen; Approval bestätigen; erneut aufrufen. | Liste enthält die freigeschalteten Katalog-Tools. Erster Aufruf: `approval_required` mit Invocation-Hash. Nach Approval: Ergebnis korrekt, `isError: false`. Audit-Eintrag zum Aufruf vorhanden (Account, Tool, Zeit; keine Eingabewerte im Klartext, falls so vorgesehen). |
| 5 | **Ablehnung einmal prüfen.** Verbindung entfernen/neu verbinden und auf der Consent-Seite „Deny“ wählen. | Erwartet nach dem #6-Fix (lokal, noch nicht deployt): Client erhält `error=access_denied` und meldet die Ablehnung sofort. Live-Stand vor Deploy: Seite „Request declined“, Client wartet bis Timeout – genau notieren, was passiert und wie lange. Kein Token entsteht. |
| 6 | **Claude Desktop:** Schritte 2–5 wiederholen. | Wie oben, getrennt für Desktop notiert – insbesondere CIMD/DCR-Verhalten und Callback-Art (Loopback oder https-Redirect). |
| 7 | **Ablauf nach 600 s.** Nach Schritt 3 mehr als 10 Minuten warten, dann erneut ein Tool aufrufen. | Server antwortet 401 `invalid_token`; notieren, ob der Client selbst neu autorisiert (Browser öffnet sich), still scheitert oder einen Fehler zeigt. Bewerten: Beeinträchtigt der fehlende Refresh-Token die praktische Nutzung (z. B. Neu-Anmeldung alle 10 Min.)? Ergebnis als Empfehlung (Refresh-Token / längere TTL / so lassen) festhalten. |

Ergebnis: je Schritt „bestanden / abweichend / nicht möglich“ mit Uhrzeit und Notiz; Abweichungen
als eigene Tasks mit Wofür. Belege (geschwärzte Screenshots, Meldungstexte) in den Belegordner des
zugehörigen `pv test`.
