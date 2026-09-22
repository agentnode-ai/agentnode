# Das Profil stand vor dem Code

`alpha-capacity-queue-r1`, SHA-256 `ade2431e8790ef9c27e99bb99e7132ff9d9e081a97c51c80ce5de9cef11210fa`,
eingefroren am **2026-09-18T18:33:37Z** — bevor eine Zeile der globalen Kapazitätssteuerung
geschrieben war. 13 Kriterien, 25 Rollen.

## Warum das die Reihenfolge ist

Kriterien, die nach der Implementierung entstehen, sind eine Beschreibung dessen, was gebaut
wurde. Sie können nichts mehr ablehnen. Der Hash und das Datum stehen hier, damit sich das
nachrechnen lässt statt geglaubt werden zu müssen.

## Was aus dem letzten Bogen gelernt ist

`alpha-runtime-pin-r1` hatte **keine `roles`-Abbildung**. Der Runner gibt jedem Eingang, den ein
Profil nicht auffuehrt, die niedrigste Autorität — `context` — und Kontext kann nichts belegen.
Neun Kriterien verlangten Belege, und keine Einreichung konnte welche liefern.
`ALPHA-RUNTIME-PIN-0002` endete allein deshalb auf BLOCK, und es brauchte zwei versionierte
Korrekturen (`r2`, `r3`), um das zu heilen.

Dieses Profil trägt die Abbildung von Anfang an, und sie ist absichtlich großzügig: eine Rolle
ohne zugehörige Datei ist harmlos, eine Datei ohne Rolle ist ein Beleg, der nichts belegt.

## Was das Profil NICHT fragt

Ob ein Preis richtig ist. Der Transport zu einer zweiten Maschine, die Mandantentrennung, Egress,
Identität, Zahlung, Domain und TLS stehen ausdrücklich außerhalb. Der Runtime-Bogen ist
abgeschlossen und wird hier nicht wieder geöffnet.
