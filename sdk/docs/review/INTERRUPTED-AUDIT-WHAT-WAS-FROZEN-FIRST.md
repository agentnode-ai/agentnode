# Das Profil stand vor dem Code

`interrupted-audit-record-r1`, SHA-256
`76d7356f0d851f8bc47a29f3c5412a8fd76e8823c50d0ca218ee03fb3c770134`,
eingefroren am **2026-09-21** — bevor eine Zeile der Behebung geschrieben war. 11 Kriterien,
25 Rollen, alle blockierend.

## Was beobachtet wurde

Beim Messen der Kapazitäts-Warteschlange: ein Auftrag, den ein Neustart des Gateways unterbricht,
erzeugt **keine Zeile** im signierten Verbrauchsprotokoll.

Abgerechnet wird dadurch nichts — kein Kunde zahlt zu viel — und das Gateway kann anschließend
noch beantworten, was aus dem Auftrag wurde, weil es ihn aus dem Ledger wieder aufbaut. Auf der
Alpha nachgestellt: alle drei unterbrochenen Läufe waren als `interrupted` abfragbar.

Aber das signierte, verkettete Protokoll — genau das, was ein zahlender Kunde als Nachweis
bekäme, was dieser Dienst getan hat — enthält den Auftrag **gar nicht**.

## Warum das für ein Nachweisprodukt zählt

Ein Auftrag, der im Protokoll fehlt, ist die eine Art Auftrag, die niemand prüfen kann: nicht
durch Lesen, nicht durch Zählen, nicht durch Abgleich einer Rechnung. Und die Warteschlange macht
den Fall gewöhnlich statt selten — Aufträge, die beim Neustart warten, sind jetzt normal.

## Warum das die Reihenfolge ist

Kriterien, die nach der Implementierung entstehen, beschreiben nur, was gebaut wurde. Hash und
Datum stehen hier, damit sich die Reihenfolge nachrechnen lässt.

Eingefroren **vor** dem Verdikt zum Warteschlangen-Bogen, aus demselben Grund wie beim
Schwesterprofil: der richtige Zeitpunkt ist der früheste, zu dem noch kein Code existiert.

## Was dieses Profil NICHT fragt

Ob ein Ende richtig **klassifiziert** wird, wenn ein Container früh stirbt — das ist
`early-ending-success-r1`. Die beiden dürfen nicht füreinander einstehen: eine vollständige
Abrechnungsspur voller falsch benannter Enden ist kein Nachweis, und ein richtig benanntes Ende,
das im Protokoll fehlt, auch nicht.

Ebenfalls nicht: der Deckel und die Warteschlange (`alpha-capacity-queue-r1`), Mandantentrennung,
Transport, Egress, Identität, Zahlung, Domain, TLS, ob ein Preis richtig ist, und wie lange
Aufzeichnungen aufbewahrt werden.
