# Das Profil stand vor dem Code

`early-ending-success-r1`, SHA-256
`857a961b0c522d9362d1d4d3719324452d909be6fc6fd2a22d703bb6dac67a1c`,
eingefroren am **2026-09-21** — bevor eine Zeile der Behebung geschrieben war. 10 Kriterien,
23 Rollen, alle blockierend.

## Was beobachtet wurde

Beim Messen der Kapazitäts-Warteschlange, also beim Messen von etwas anderem: Sandbox-Container,
die `time.sleep(120)` ausführen sollten, endeten mehrfach nach rund **11,3 Sekunden**. Der Klient
sah `-9`, und die signierte Verbrauchszeile sagte:

    state=finished   seconds=11.332   outcome=succeeded

Derselbe Pfad schreibt korrekt `timed_out`, wenn ein Zeitlimit einen Lauf beendet — das ist
gemessen, nicht vermutet. Die Maschinerie, Enden auseinanderzuhalten, existiert also und hat
diesen Fall nicht erreicht.

## Zwei Fragen, und es ist nicht dieselbe

**Warum endete der Lauf früh** — und **warum wurde ein Ende, das kein Erfolg war, als einer
aufgeschrieben.**

Die zweite ist der Defekt, der für einen Dienst zählt, der nach Laufzeit abrechnet und einen
Nachweis darüber verkauft, was passiert ist. Die erste kann sich als Umgebungsproblem
herausstellen. Eine Behebung, die nur die erste beantwortet, ist nicht fertig — und eine, die nur
die zweite beantwortet, muss das sagen, statt die erste stillschweigend fallen zu lassen.

## Warum das die Reihenfolge ist

Kriterien, die nach der Implementierung entstehen, sind eine Beschreibung dessen, was gebaut
wurde. Sie können nichts mehr ablehnen. Hash und Datum stehen hier, damit sich das nachrechnen
lässt statt geglaubt werden zu müssen.

Eingefroren wurde **vor** dem Verdikt zum Warteschlangen-Bogen, nicht danach. Der richtige
Zeitpunkt für ein prospektives Profil ist der früheste, zu dem noch kein Code existiert; zu warten
hätte nichts sicherer gemacht und nur die Gelegenheit vergrößert, die Kriterien an eine
inzwischen entstandene Lösung anzupassen.

## Was dieses Profil NICHT fragt

Ob der Deckel und die Warteschlange richtig sind — das ist `alpha-capacity-queue-r1` und wird hier
nicht wieder geöffnet. Ob die Sandbox richtig dimensioniert ist. Ob frühe Enden überhaupt noch
vorkommen: dieses Profil handelt davon, was über ein Ende **aufgeschrieben** wird, nicht davon,
jedes Ende zu verhindern.

Und ausdrücklich nicht die Vollständigkeit der Abrechnungsspur — das ist
`interrupted-audit-record-r1`. Die beiden dürfen nicht füreinander einstehen.
