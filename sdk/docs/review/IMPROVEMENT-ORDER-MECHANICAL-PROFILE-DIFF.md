# Verbesserungsauftrag: Profiländerungen mechanisch und unabhängig prüfen

**Status:** offen, nicht begonnen. **Blockiert nichts.** Insbesondere blockiert dieser Auftrag
weder das R2-Deployment noch irgendeine laufende Arbeit.

## Warum

`GOVERNANCE-LIMIT-A-PROFILE-CANNOT-REVIEW-ITSELF.md` beschreibt den Befund: ein Prüfprofil ist
eine Anweisung an einen Prüfer, also kann es nicht als Evidenz eingereicht werden, ohne die
Injektionsregel zu verletzen. Heute wird eine Profilkorrektur deshalb von Hand nachgerechnet, und
„von Hand nachgerechnet" ist genau die Sorte Nachweis, die dieses Repository sonst nicht akzeptiert.

Gebraucht wird kein Prüfer, der den Text *liest* und beurteilt. Gebraucht wird ein Programm, das
die Änderung *rechnet* und ohne jede Meinung über den Inhalt sagen kann, was sie mit der Strenge
gemacht hat.

## Was es können muss

Eingabe: zwei Profilfassungen. Ausgabe: ein Urteil, das keine Sprachbeurteilung enthält.

1. **Die strukturelle Differenz, vollständig.** Welche Kriterien sind byteidentisch, welche
   geändert, welche hinzugekommen, welche verschwunden. Ein verschwundenes Kriterium ist die
   gefährlichste Änderung und muss als solche herausstechen.

2. **Die Richtung jeder Änderung, mechanisch bestimmt.** Für jedes geänderte Kriterium: enthält
   die neue Fassung die alte unverändert und fügt hinzu (**verschärfend oder neutral**), oder
   wurde am alten Text etwas entfernt oder ersetzt (**möglicherweise aufweichend — Handarbeit
   nötig**). Der Präfix-Fall ist entscheidbar und deckt den häufigsten Korrekturtyp ab; alles
   andere darf das Werkzeug ausdrücklich NICHT beurteilen, sondern muss es als unentscheidbar
   melden. Ein Werkzeug, das Aufweichungen zu erkennen behauptet, die es nicht erkennen kann, ist
   schlimmer als keines.

3. **Die nicht-kriterialen Felder.** `blocking`, `stage`, `roles`, `out_of_scope`,
   `verdict_must_state`. Ein Kriterium von `blocking: true` auf `false` zu setzen, ohne den Text
   anzufassen, ist die leiseste denkbare Aufweichung und muss laut sein.

4. **Die Kette.** `corrects` muss auf eine Fassung zeigen, die existiert; die korrigierte Fassung
   muss unverändert daneben liegen; und jede Korrektur muss mindestens eine Entscheidung nennen,
   die ein eigenes PASS trägt.

5. **Kein Urteil über Inhalt.** Das Werkzeug liest keinen Text, es vergleicht ihn. Damit kann es
   von dem Text, den es prüft, nicht angewiesen werden — was der ganze Punkt ist.

## Wie sein eigener Nachweis aussieht

Wie jeder andere Mechanismus hier: mit Gegenproben, die rot werden müssen.

* eine Fassung, die ein Kriterium **streicht** → das Werkzeug muss es nennen;
* eine, die `blocking` auf `false` setzt → muss es nennen;
* eine, die einen Satz **mitten aus** einem Kriterium entfernt → muss als „möglicherweise
  aufweichend" gemeldet werden, nicht als neutral;
* eine, die nur **anfügt** → muss als verschärfend-oder-neutral durchgehen, sonst ist das
  Werkzeug so streng, dass niemand es benutzt;
* und `r4 → r5`, der Fall, aus dem dieser Auftrag entstand → sechs identisch, zwei anfügend.

## Was dieser Auftrag ausdrücklich nicht löst

Er macht Profiländerungen nicht sicher. Er macht sie **nachrechenbar**, und zwar von etwas, das
nicht überredet werden kann. Ob eine verschärfende Änderung inhaltlich richtig ist, bleibt eine
Frage für Menschen und für die Entscheidung, die sie begründet. Das Werkzeug beantwortet nur die
Frage, die heute von Hand beantwortet wird: **was hat sich geändert, und in welche Richtung.**
