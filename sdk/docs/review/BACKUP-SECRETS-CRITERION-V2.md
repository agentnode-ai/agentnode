# Geheimnisse und Backups, Kriterium v2 — „nicht im Klartext" statt „nicht vorhanden"

`ALPHA-R2-DECISION-BACKUP-0001` hat **PASS auf Option A** zurückgegeben. Dieses Dokument ist die
versionierte Fassung des Kriteriums, die daraus folgt. Es ersetzt **nicht** die Sicherheitsabsicht
von P1, sondern präzisiert ihren Nachweis dort, wo die absolute Lesart mit P6 desselben Profils
nicht gleichzeitig erfüllbar war.

Die Vorgeschichte steht vollständig in `.git/codex-review/`: der Befund
`F-P1-BACKUP-CONTAINS-PRIVATE-SECRETS`, die Gründerauflage, die ihm vorausging, und die geprüfte
Entscheidung mit ihren drei Feststellungen.

## Warum die absolute Lesart ersetzt wird

Ein Backup dieses Gateways enthält den Metering-Signierschlüssel, die private Hälfte des
TLS-Zertifikats und den Schlüssel, der die Betreiber-Policy authentifiziert.

Ein Backup **ohne** sie ist kein Backup dieses Gateways. Die Wiederherstellung daraus erzeugte eine
Instanz mit anderer Identität: die Metering-Kette bräche an der Nahtstelle, gepaarte Geräte
vertrauten dem Zertifikat nicht mehr, die Betreiber-Policy wäre nicht mehr die geprüfte. Genau das
ist die Eigenschaft, die P6 verlangt und die der Drill misst.

Ein Kriterium, das mit einem anderen desselben Profils nicht gleichzeitig erfüllbar ist, ist kein
strenges, sondern ein widersprüchliches. Es wird deshalb ersetzt — sichtbar, versioniert und
geprüft, nicht still.

## Das Kriterium, fünf Teile

**(1) Nichts liegt lesbar im Archiv.** Keine der Geheimnis-Bytes erscheint im ruhenden Archiv —
geprüft gegen die **Rohbytes**, nicht gegen ihre druckbare Form. *Nachweis:
`test_no_shape_survives_in_a_sealed_archive`, pro Commit.*

**(2) Der Schlüssel liegt nie dabei.** Nicht im Archiv, nicht im Sicherungsverzeichnis, nicht im
Zustandsverzeichnis. Ein Schlüssel an einem dieser Orte wird beim Sichern **verweigert**, nicht
gewarnt. *Nachweis: `key_is_somewhere_else()` im Sicherungsskript und dessen Tests.*

**(3) Fail-closed vor der Extraktion.** Ein fehlender, falscher oder vertauschter Schlüssel und
ein manipuliertes, abgeschnittenes oder untergeschobenes Archiv scheitern, **bevor** irgendetwas
entpackt wird, mit einer Meldung, die den Grund nicht unterscheidet. *Nachweis:
`test_a_sealed_backup.py`, pro Commit.*

**(4) Nichts davon steht irgendwo geschrieben.** Weder Manifest noch Log noch Fehlermeldung
enthalten Schlüssel oder Passphrase. *Nachweis: dieselben Tests, plus die Planted-Secret-Suite
gegen jede Senke.*

**(5) Und die Geheimnisse sind wirklich drin.** Der Drill entschlüsselt, zerstört, stellt auf
frischem Zustand wieder her, startet das Gateway und prüft eine Berechtigung, die **vor** dem
Backup ausgestellt wurde. Ohne diesen Teil wäre (1) auch dann erfüllt, wenn das Archiv die
Geheimnisse gar nicht enthielte — und das ist der eine Weg, auf dem dieses Kriterium sich selbst
belügen könnte. *Nachweis: `restore-transcript.txt`, ausgeführt gegen die Alpha.*

## Was dieses Kriterium ausdrücklich nicht etabliert

Die drei Feststellungen der Entscheidung, in ihren eigenen Worten, weil ein Kriterium seine
Grenzen mitführen muss:

* **RISK-COLOCATED-KEY** (HIGH) — „Option A does not protect a copied archive when its decryption
  key is also compromised or stored with it. Its security depends on continued separation of
  archive and key storage." Deshalb ist Teil (2) eine Verweigerung und keine Empfehlung: die
  Trennung ist die Eigenschaft, nicht die Verschlüsselung.
* **RISK-PLAINTEXT-WORDING** (MEDIUM) — „The revised wording could be misread as weakening P1
  unless its rationale and associated controls remain attached to the versioned criterion."
  Deshalb steht die Begründung in diesem Dokument und nicht in einer Fußnote, und deshalb nennt
  Teil (5) ausdrücklich, was ohne ihn schiefginge.
* **LIMIT-POINT-IN-TIME** (MEDIUM) — „establishes neither post-backup continuity nor retroactive
  protection of previously copied archives after key rotation." Ein Archiv, das jemand vor dem
  Schlüsselwechsel kopiert hat, bleibt mit dem alten Schlüssel lesbar. Das ist die Eigenschaft
  jedes Schlüsselwechsels und wird hier nicht bestritten.

Und was ohnehin gilt: wer Archiv **und** Schlüssel hat, hat das Gateway. Das ist die Eigenschaft
jeder Verschlüsselung, keine Schwäche dieser.
