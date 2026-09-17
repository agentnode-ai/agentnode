# Existenz-Isolation, Kriterium v2 — an das Bedrohungsmodell gebunden

`THREAT-MODEL-CRITERION-DECISION-0001` hat **PASS auf Option A** zurückgegeben. Dieses Dokument
ist die versionierte Fassung des Kriteriums, die daraus folgt. Es ersetzt **nicht** die
Sicherheitsabsicht von T2 und T6, sondern bindet ihren Nachweis an das, was ein entfernter
Angreifer tatsächlich hat.

Die Vorgeschichte steht vollständig in `.git/codex-review/r2-evidence/`: die erste rote
Beobachtung unverändert, die Experimente, die die verzerrte Statistik als Ursache belegen, und
der erste Holdout, der nach der eingefrorenen Regel ungültig war, weil die Sensitivitätskontrolle
nicht ansprach.

## Warum der alte Grenzwert ersetzt wird

Der eingefrorene Kontrolleffekt war **eine zusätzliche Dictionary-Abfrage**. Unter Linux misst
sie rund **40 ns**. Der Nachweis lautete damit: 40 ns innerhalb einer Operation von ~1400 ns
zuverlässig erkennen, gegen ein Lauf-zu-Lauf-Rauschen von ~130 ns.

Gemessen wurde an einer Funktion im Prozess. Was ein entfernter Angreifer sieht, ist eine
vollständige authentifizierte HTTPS-Anfrage: Verbindungsaufbau oder Multiplexing, HTTP-Parsing,
Dispatch, ein Audit-Schreibvorgang auf Platte, Signatur, Serialisierung, Netzwerkweg. 40 ns darin
sind um Größenordnungen kleiner als die Varianz jedes einzelnen dieser Schritte.

Ein Grenzwert, der für den Angreifer, gegen den er schützen soll, keine Bedeutung hat, ist kein
Produktnachweis. Er wird deshalb ersetzt — sichtbar, versioniert und geprüft, nicht still.

## Das Kriterium, sieben Teile

**(1) Kein globaler Existenz-Lookup.** Ressourcen werden ausschließlich im authentifizierten
Account-Namensraum gesucht — weder davor noch danach global. *Nachweis: Strukturtest, pro Commit.*

**(2) Identische Antworten und Nebenwirkungen.** Ein fremder und ein nicht vorhandener Bezeichner
erzeugen denselben HTTP-Status, denselben Antwortkörper, dieselbe Antwortlänge, denselben
Signaturpfad, dieselbe Auditwirkung, dieselbe Zählerwirkung und dasselbe Fehlervokabular.
*Nachweis: deterministische Tests über jede Oberfläche, pro Commit.*

**(3) Entropie und Normalisierung.** Bezeichner sind nicht erratbar und werden vor dem Lookup
accountgebunden auf feste Länge normalisiert. *Nachweis: Strukturtest, pro Commit.*

**(4) Begrenzung systematischer Versuche.** Authentifizierung, Rate-Limits und Monitoring
begrenzen, wie oft jemand fragen kann, ohne aufzufallen. *Nachweis: die Admission-Tests.*

**(5) Kein existenzabhängiger Anwendungszweig.** Ein interner Strukturtest beweist, dass der
Codepfad nicht davon abhängt, ob das Objekt eines anderen Accounts existiert. *Pro Commit.*

**(6) Periodischer Blackbox-Test am echten Endpunkt.** Am realen TLS-Endpunkt, unter dem realen
Rate-Limit, wird geprüft, ob ein reproduzierbares und praktisch nutzbares Signal existiert.
*Nicht pro Commit. Spezifikation unten, eingefroren vor der ersten Erhebung.*

**(7) Der Mikrobenchmark bleibt — als Diagnose, nicht als Tor.** Er darf die Gleichheit des
Lookup-Mechanismus untersuchen. Er ist **kein Per-Commit-Gate** und **keine Behauptung
physikalisch konstanter Laufzeit**.

## Die Blackbox-Spezifikation, eingefroren vor neuen Daten

Erhoben wird gegen den laufenden Gateway über HTTPS, mit einer gültigen Berechtigung, gegen
`status` mit einem fremden und einem nicht vorhandenen Lauf.

| | | warum |
|---|---|---|
| Bezeichnerpaare | **32** | Paarweise, zufällig und verblindet zugeordnet, wie im Mikrobenchmark. |
| Anfragen pro Bezeichner | **64** | Unter dem realen Rate-Limit; die Begrenzung ist Teil dessen, was geprüft wird, und wird nicht umgangen. |
| Wiederholungen | **3 unabhängige Läufe** | Verschiedene Verbindungen, verschiedene Zeitpunkte. |
| Statistik | paarweiser Permutationstest, **10 000** Sign-Flips | Dieselbe Familie wie im Mikrobenchmark, aus demselben Grund: unverzerrt, ohne gewählte Marge. |
| Entscheidung | zweiseitiges **p ≥ 0.01** *und* 99-%-Bootstrap-Intervall über der Median-Differenz, das die Null enthält | Eine Entscheidung über alle Läufe, per Fisher kombiniert. |
| Sensitivitätskontrolle | eine absichtliche Verzögerung in Höhe **eines Audit-Schreibvorgangs** auf dem fremden Pfad | Am Endpunkt ist das die kleinste Einheit, die über TLS plausibel sichtbar wäre — nicht eine Dictionary-Abfrage, die dort im Rauschen verschwindet. |
| Gültigkeit | Spricht die Kontrolle nicht an, ist der Lauf **ungültig** | Dieselbe Regel wie zuvor, und sie hat dort funktioniert. |

Keine dieser Zahlen stammt aus einer bereits gesehenen Messung. Die ungültigen Läufe unter
`timing-void-1/` und die erste rote Beobachtung werden für nichts davon verwendet.

## Was dieses Kriterium ausdrücklich nicht etabliert

Die drei Feststellungen der Entscheidung, in ihren eigenen Worten und hier wiederholt, weil ein
Kriterium seine Grenzen mitführen muss:

* **F-001** — Es ist auf einen *entfernten, authentifizierten* Angreifer bezogen. Es etabliert
  keine konstante Laufzeit in CPython und keinen Schutz gegen einen Angreifer auf derselben
  Maschine. Ändert sich das Bedrohungsmodell, ist das Kriterium neu zu betrachten. Genau deshalb
  wird der Mikrobenchmark in Teil (7) behalten und nicht gestrichen: für einen lokalen Angreifer
  wäre er die richtige Frage.
* **F-002** — Die alte Statistik zu verwerfen beweist nicht die Abwesenheit jedes Zeitkanals.
  Diese Unsicherheit wird durch (6) und (7) verwaltet, nicht weggeredet.
* **F-003** — Alles hängt an (1) und (2). Bricht die strukturelle Isolation oder die Gleichheit
  des von außen Sichtbaren, ist der Kanal wieder da, gleich was eine Zeitmessung sagt. **Keine
  Messung kann das kompensieren.**

## Was ein Angreifer zugleich leisten müsste

Gültiger Zugang; eine fremde, hochentropische Kennung, die er nicht erraten kann und also
anderswoher besitzen muss; genug Anfragen unter dem Rate-Limit und am Monitoring vorbei; und ein
Signal aus Netzwerk-, TLS-, Scheduler- und Laufzeitrauschen.

Der Gewinn wäre allein die **Bestätigung**, dass eine Kennung, die er bereits hat, existiert.
Nicht wem sie gehört, nicht was sie enthält, nicht was sie getan hat.
