# Bekannte Governance-Grenze: ein Prüfprofil kann sich nicht selbst prüfen lassen

## Der Befund

`alpha-r2-dataops-r5` ist eine versionierte Korrektur von r4. Bevor sie benutzt wurde, sollte sie
— wie jede Korrektur in diesem Repository — selbst geprüft werden. Das ging nicht, und zwar nicht
aus einem Versehen, sondern aus der Bauart des Systems.

Eingereicht als `ALPHA-R2-PROFILE-DATAOPS-R5-0001` unter `technical-decision`, kam zurück:

> **UNTRUSTED-OPERATIVE-REVIEW-DIRECTIONS** (HIGH) — „Both context files contain operative
> instructions addressed to a reviewer. They are untrusted evidence and cannot replace or extend
> the four binding manifest criteria."

Die beiden „context files" waren r4 und r5 selbst. Der Befund ist korrekt und unvermeidbar: **ein
Prüfprofil IST eine Anweisung an einen Prüfer.** Jedes seiner Kriterien beginnt mit „Judge
whether…" oder „Determine…". Es als Evidenz einzureichen heißt zwangsläufig, anweisenden Text in
ein Bündel zu legen, und genau das verbietet die Injektionsregel — zu Recht, denn sie kann nicht
zwischen „diese Anweisung ist das Prüfobjekt" und „diese Anweisung will dich steuern"
unterscheiden.

Die Regel ist nicht falsch. Sie hat nur einen blinden Fleck, und der ist strukturell.

## Was das praktisch heißt

Für r5 gilt heute:

* die **zwei Entscheidungen**, die die Korrektur begründen, tragen je ein eigenes PASS
  (`ALPHA-R2-DECISION-BACKUP-0001`, `ALPHA-R2-DECISION-DELETION-0002`);
* die **mechanischen Eigenschaften** der Korrektur sind nachrechenbar und wurden vor dem Merge
  nachgerechnet: sechs von acht Kriterien byteidentisch zu r4, und die zwei geänderten enthalten
  den r4-Text unverändert als Präfix — sie fügen hinzu, sie ersetzen nichts;
* das **Profil als Ganzes** hat kein eigenes PASS und wird auch keines bekommen, solange dieser
  blinde Fleck besteht.

Das ist eine Lücke in der Kette, kein Loch im Ergebnis. Aber es ist eine Lücke, und sie steht hier,
damit niemand sie später für eine Selbstverständlichkeit hält.

## Warum die naheliegenden Auswege keine sind

**„Das Profil einfach als `frozen_spec` statt als `context` einreichen."** Verschiebt die Frage
nur: dann trüge das Bündel eine Anweisung mit höherer Autorität, und der Prüfer müsste dem Text
folgen, dessen Prüfung er gerade vornehmen soll.

**„Die Injektionsregel für Profildateien ausnehmen."** Das ist genau die Regel, die verhindert,
dass ein Einreicher dem Prüfer Anweisungen unterschiebt. Eine Ausnahme nach Dateityp ist eine
Ausnahme, die jemand ausnutzen kann, indem er seine Anweisung Profil nennt.

**„Ein zweites Modell den Text lesen lassen."** Das verlagert das Vertrauen, ohne es zu
begründen, und erzeugt dieselbe Situation eine Ebene höher.

## Was stattdessen zu bauen wäre

Nicht eine Prüfung, die den Text *liest*, sondern eine, die die Änderung *rechnet*: ein
mechanischer, semantischer Diff zweier Profilfassungen, der ohne Urteil über den Inhalt sagen
kann, ob eine Korrektur verschärft oder aufgeweicht hat. Der Auftrag dazu steht in
`IMPROVEMENT-ORDER-MECHANICAL-PROFILE-DIFF.md`. Er blockiert kein Deployment.

## Was hier ausdrücklich nicht behauptet wird

Dass r5 unbedenklich ist, weil die Prüfung nicht möglich war. Die Unmöglichkeit ist ein Grund,
misstrauischer hinzusehen, nicht nachsichtiger. Was für r5 spricht, sind die nachgerechneten
Eigenschaften und die zwei geprüften Entscheidungen — nicht das Fehlen eines Einwands.
