"use strict";
/* ---------------------------------------------------------------------------
 * The console.
 *
 * A separate file rather than an inline script, and that is a security decision
 * rather than tidiness: a content security policy that has to allow inline code
 * allows ANY inline code, which is most of what an injection wants. With this
 * in its own file the policy can say `script-src 'self'` and mean it.
 *
 * This page holds no credential. It never sees a device token -- the one place
 * a token is written out is a file the gateway streams straight to disk -- and
 * its session lives in a cookie this code cannot read. What it does keep is a
 * confirmation value, in memory, for as long as the page is open. Reloading
 * fetches a new one; closing the tab loses it, which is the point.
 * ------------------------------------------------------------------------- */

var S = {
  csrf: "", device: "", deviceName: "", caps: null,
  tab: "setup", step: 0, way: null, jobs: [], setup: null, err: null,
  // Everything this page has scheduled, and which run of the page scheduled it. A poller that
  // keeps going after somebody signs out is work nobody owns -- it still holds the job it was
  // watching, and it still asks the gateway about it. Daemon-ness is not ownership in a browser
  // either: the tab stays open.
  epoch: 0, timers: []
};

function later(fn, ms){
  var mine = S.epoch;
  var id = setTimeout(function(){
    S.timers = S.timers.filter(function(t){ return t !== id; });
    if(S.epoch === mine) fn();
  }, ms);
  S.timers.push(id);
  return id;
}

function stopEverythingScheduled(){
  S.epoch += 1;
  S.timers.splice(0).forEach(clearTimeout);
}

/* --- talking to the sandbox ---------------------------------------------- */

/* Which operations travel as POST. The rule is the contract's, not this page's: an operation
 * that changes something OR takes any parameter goes in the body, because a parameter in a URL
 * ends up in logs, in history and in a referrer.
 *
 * This is a copy of that rule, so it can fall out of step with it -- and it did. `devices.invite`
 * was declared, the button was added, and this table was not, so "Weiteres Gerät hinzufügen" sent
 * a GET and every customer trying to add their second machine met "Diese Anfrage war nicht in
 * Ordnung." Nothing caught it, because the tests behind it drove the dispatcher rather than the
 * page. `test_routes_register.py` now compares this table against the contract, so the next
 * operation somebody adds cannot arrive here silently wrong. */
var NEEDS_POST = {prepare:1, submit:1, status:1, result:1, cancel:1, usage:1,
                  "devices.revoke":1, "devices.invite":1, "devices.uninvite":1,
                  "sessions.end":1,
                  "connections.enrol":1, "connections.check":1};

function call(op, params){
  var post = !!NEEDS_POST[op];
  var init = {method: post ? "POST" : "GET", headers: {}, cache: "no-store",
              credentials: "same-origin", referrerPolicy: "no-referrer"};
  if(post){
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(params || {});
  }
  // Sent on everything, though the gateway only requires it for what changes
  // state. A header is attached by this page; the cookie is attached by the
  // browser. A request from another site can manage the second and not the first.
  if(S.csrf) init.headers["X-AgentNode-Confirm"] = S.csrf;
  return fetch("/v1/op/" + op.replace(/\./g, "/"), init).then(function(r){
    return r.json().catch(function(){ return {}; }).then(function(body){
      if(body && body.refused) throw refusal(body);
      if(!r.ok) throw plain("Die Sandbox hat nicht geantwortet, wie sie sollte.");
      return body;
    });
  }, function(){ throw plain("Die Sandbox ist gerade nicht erreichbar."); });
}

function refusal(body){
  var e = new Error(body.because || "abgelehnt");
  e.refused = body.refused; e.because = body.because; e.what_to_do = body.what_to_do;
  return e;
}
function plain(msg){ var e = new Error(msg); e.refused = "unreachable"; return e; }

/* Exactly one recommended action per error. A person reading an error wants to
 * know what to press, not a list of possibilities. */
var SAYS = {
  not_authenticated: ["Dieser Zugang gilt nicht mehr.",
    "Das passiert, wenn eine Sitzung beendet, ein Zugang zurückgezogen oder die Einladung " +
    "abgelaufen ist. Mit einer neuen Einladung geht es weiter.", "again"],
  not_permitted: ["Dieses Gerät darf das nicht.",
    "Bitte bei der Person nachfragen, die die Sandbox betreibt.", "retry"],
  unknown_operation: ["Das kennt diese Sandbox nicht.",
    "Meist ist die Seite älter als die Sandbox oder umgekehrt. Ein Neuladen holt die " +
    "aktuelle Fassung.", "retry"],
  upgrade_required: ["Dieser Zugang ist zu alt dafür.",
    "Die Sandbox erwartet etwas, das dieser Zugang noch nicht mitschickt. Ein Neuladen holt " +
    "die aktuelle Fassung der Seite.", "retry"],
  gateway_stopped: ["Die Sandbox nimmt gerade keine Arbeit an.",
    "Der Betrieb wurde angehalten. Sobald er wieder läuft, funktioniert alles Weitere " +
    "unverändert.", "retry"],
  over_a_ceiling: ["Das Kontingent für dieses Zeitfenster ist aufgebraucht.",
    "Laufende Aufträge sind nicht betroffen. Sobald das Fenster weiterrückt, geht es wieder.",
    "usage"],
  refused_by_policy: ["Das ist hier nicht erlaubt.",
    "Die Sandbox lässt diesen Auftrag nicht zu. Ein einfacherer Auftrag ohne Netzzugriff " +
    "geht meist.", "retry"],
  sandbox_unavailable: ["Die Sandbox selbst läuft gerade nicht.",
    "Ohne sie wird nichts ausgeführt — das ist so gewollt. Bitte später noch einmal versuchen.",
    "retry"],
  disclosure_required: ["Dafür fehlt Ihre Zustimmung.",
    "Bitte noch einmal starten: Sie bekommen zuerst zu sehen, was passieren würde.", "retry"],
  no_such_run: ["Das gibt es hier nicht (mehr).", "Bitte die Übersicht neu laden.", "retry"],
  not_finished: ["Der Auftrag läuft noch.", "Das Ergebnis erscheint, sobald er fertig ist.",
    "retry"],
  malformed: ["Diese Anfrage war nicht in Ordnung.", "Bitte die Seite neu laden.", "retry"],
  unreachable: ["Keine Verbindung zur Sandbox.",
    "Prüfen, ob der Rechner läuft, auf dem die Sandbox betrieben wird.", "retry"]
};
function explain(e){
  var k = SAYS[e && e.refused];
  var said = [];
  // The sandbox's OWN words, when it sent any. The table above says what KIND of thing
  // happened, in the reader's language, and that is what somebody reads first. It cannot say
  // what happened to THEM: "die Sandbox nimmt keine Arbeit an" and "Ihr Konto ist gesperrt,
  // weil ..." arrive under the same refusal name, and showing only the first tells a suspended
  // customer something that is not true of them and leaves them nothing to do.
  if(e && e.because) said.push(e.because);
  if(e && e.what_to_do) said.push(e.what_to_do);
  if(k) return {title:k[0], help:k[1], fix:k[2], said:said};
  return {title:"Etwas hat nicht funktioniert.",
          help:(e && e.message) || "Bitte noch einmal versuchen.", fix:"retry", said:said};
}

/* --- little helpers ------------------------------------------------------- */

function el(tag, attrs, kids){
  var n = document.createElement(tag);
  for(var k in (attrs||{})){
    var v = attrs[k];
    if(v === null || v === undefined || v === false) continue;
    // innerHTML is never assigned anything but the empty string in this file -- it is
    // used to CLEAR a node and never to fill one. Everything a person or a gateway
    // supplies becomes a text node, so a device called "<script>..." is a device with
    // an odd name and nothing more. A test checks the assignment, not this comment.
    if(k === "text") n.textContent = v;
    else if(k.slice(0,2) === "on") n.addEventListener(k.slice(2), v);
    else if(v === true) n.setAttribute(k, "");
    else n.setAttribute(k, v);
  }
  (kids||[]).forEach(function(c){ if(c) n.appendChild(c); });
  return n;
}
function hex(n){
  var a = new Uint8Array(n); crypto.getRandomValues(a);
  return Array.prototype.map.call(a, function(b){ return ("0"+b.toString(16)).slice(-2); }).join("");
}
function digest(text){
  return crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)).then(function(buf){
    return Array.prototype.map.call(new Uint8Array(buf),
      function(b){ return ("0"+b.toString(16)).slice(-2); }).join("");
  });
}
function b64(text){
  var bytes = new TextEncoder().encode(text), s = "";
  for(var i=0;i<bytes.length;i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}
function ago(ts){
  if(!ts) return "noch nie benutzt";
  var s = Math.max(0, Math.floor(Date.now()/1000 - ts));
  if(s < 60) return "gerade eben";
  if(s < 3600) return "vor " + Math.floor(s/60) + " Min.";
  if(s < 86400) return "vor " + Math.floor(s/3600) + " Std.";
  return "vor " + Math.floor(s/86400) + " Tagen";
}
function guessDeviceName(){
  var ua = navigator.userAgent || "";
  if(/iPhone/.test(ua)) return "Mein iPhone";
  if(/iPad/.test(ua)) return "Mein iPad";
  if(/Android/.test(ua)) return "Mein Android-Telefon";
  if(/Macintosh/.test(ua)) return "Mein Mac";
  if(/Windows/.test(ua)) return "Mein Windows-PC";
  if(/Linux/.test(ua)) return "Mein Linux-Rechner";
  return "Mein Gerät";
}

/* Announced to a screen reader without moving anybody's focus. Status changes
 * that only exist visually are status changes some people never receive. */
function announce(what){
  var live = document.getElementById("live");
  if(live) live.textContent = what;
}
function focusHeading(){
  var h = document.querySelector("main h1");
  if(h){ h.setAttribute("tabindex", "-1"); h.focus({preventScroll:false}); }
}

/* --- the ten steps -------------------------------------------------------- */

var STEPS = ["Einrichten", "Einladung", "Gerät benennen", "Verbindung bestätigen",
             "KI auswählen", "Einrichtung", "Test", "Bestätigung", "Erster Auftrag", "Ergebnis"];

var WAYS = [
  {id:"bridge", label:"Claude Code oder Codex auf meinem Rechner",
   hint:"Werkzeuge über eine lokale Brücke — die verbreitetste Variante.", channel:"mcp"},
  {id:"mcp", label:"Ein anderes Programm, das Werkzeuge anbinden kann",
   hint:"Alles, was Werkzeuge über MCP anspricht, verbindet sich direkt.", channel:"mcp"},
  {id:"rest", label:"Eigene Software oder ein eigenes Skript",
   hint:"Spricht die Sandbox über ihre normale Schnittstelle an.", channel:"rest"},
  {id:"cli", label:"Die Kommandozeile auf meinem Rechner",
   hint:"Für Leute, die ohnehin im Terminal arbeiten.", channel:"cli"},
  {id:"none", label:"Ein reines Chatfenster ohne Werkzeuge",
   hint:"Eine KI, die nichts außerhalb ihres Chats aufrufen kann.", channel:""}
];

/* --- rendering ------------------------------------------------------------ */

function show(nodes){
  var m = document.getElementById("main");
  m.innerHTML = "";
  (Array.isArray(nodes)?nodes:[nodes]).forEach(function(n){ if(n) m.appendChild(n); });
  drawTabs(); drawFoot();
}
function drawTabs(){
  var nav = document.getElementById("tabs");
  nav.innerHTML = "";
  if(!S.csrf){ nav.hidden = true; return; }
  nav.hidden = false;
  [["setup","Übersicht"],["devices","Verbindungen"],["sessions","Anmeldungen"],
   ["usage","Verbrauch"],["safety","Sicherheit"]].forEach(function(t){
    nav.appendChild(el("button", {text:t[1], type:"button",
      "aria-current": S.tab === t[0] ? "page" : null,
      onclick: function(){ S.tab = t[0]; S.err = null; render(); focusHeading(); }}));
  });
}
function drawFoot(){
  var f = document.getElementById("foot");
  f.innerHTML = "";
  var limits = (S.caps && S.caps.what_this_does_not_establish) || [];
  if(!limits.length) return;
  f.appendChild(el("p", {text:"Was diese Sandbox ausdrücklich nicht ist:"}));
  var ul = el("ul", {});
  limits.forEach(function(l){ ul.appendChild(el("li", {text: l})); });
  f.appendChild(ul);
}
function progress(){
  var ul = el("ul", {"class":"steps",
                     "aria-label":"Fortschritt: Schritt " + (S.step+1) + " von 10"});
  for(var i=0;i<STEPS.length;i++)
    ul.appendChild(el("li", {"class": i < S.step ? "done" : (i === S.step ? "now" : "")}));
  return ul;
}
function head(title, lede){
  return el("div", {}, [
    progress(),
    el("p", {"class":"stepno", text:"Schritt " + (S.step+1) + " von 10 · " + STEPS[S.step]}),
    el("h1", {text:title}),
    lede ? el("p", {"class":"lede", text:lede}) : null
  ]);
}
function problem(e, retry){
  var w = explain(e);
  announce(w.title);
  var box = el("div", {"class":"note n-bad", role:"alert"}, [
    el("h3", {text:w.title}), el("p", {text:w.help})
  ]);
  // Marked off as coming from the sandbox rather than from this page, so a reader can tell
  // which sentence is a general explanation and which one is about them. Text nodes, like
  // everything else here.
  if(w.said && w.said.length){
    box.appendChild(el("p", {"class":"said-by", text:"Die Sandbox sagt dazu:"}));
    w.said.forEach(function(line){ box.appendChild(el("p", {text:line})); });
  }
  // Exactly one. A person meeting an error wants to know what to press.
  if(w.fix === "retry" && retry)
    box.appendChild(el("button", {"class":"b ghost", type:"button",
      text:"Noch einmal versuchen", onclick:retry}));
  else if(w.fix === "again")
    box.appendChild(el("button", {"class":"b ghost", type:"button",
      text:"Neu anmelden", onclick:function(){ signedOut(); }}));
  else if(w.fix === "usage")
    box.appendChild(el("button", {"class":"b ghost", type:"button", text:"Verbrauch ansehen",
      onclick:function(){ S.tab="usage"; S.err=null; render(); }}));
  return box;
}

/* --- step 1: willkommen --------------------------------------------------- */

function stepWelcome(){
  show([
    head("Willkommen bei AgentNode.",
         "In zehn kurzen Schritten verbinden wir dieses Gerät mit einer Sandbox — einem " +
         "abgeschirmten Platz, an dem eine KI Code ausführen kann, ohne an Ihren Rechner zu " +
         "kommen."),
    el("div", {"class":"card stack"}, [
      el("h2", {text:"Was Sie dafür brauchen"}),
      el("ul", {}, [
        el("li", {text:"Einen Einladungslink oder einen kurzen Einladungscode."}),
        el("li", {text:"Ein paar Minuten. Ihr Zugang wird nicht in diesem Browser gespeichert."})
      ]),
      el("div", {"class":"row"}, [
        el("button", {"class":"b primary", type:"button", id:"start",
          text:"Einrichtung starten",
          onclick:function(){ S.step=1; render(); focusHeading(); }})
      ])
    ])
  ]);
}

/* --- step 2: einladung ---------------------------------------------------- */

/* An invitation arrives as `…/console#code=XXXX-XXXX-XXXX`. What follows the #
 * is never sent to any server, so it cannot reach an access log or a referrer.
 * It is read once and wiped out of the address bar before anything else runs,
 * so a screenshot or a shared link carries nothing. */
function codeFromLink(){
  var frag = (location.hash || "").replace(/^#/, "");
  if(!frag) return "";
  var found = "";
  frag.split("&").forEach(function(part){
    var bits = part.split("=");
    if(bits[0] === "code" || bits[0] === "einladung")
      found = decodeURIComponent(bits.slice(1).join("="));
  });
  if(found) history.replaceState(null, "", location.pathname);
  return found;
}

function stepInvitation(prefill){
  var code = prefill !== undefined && prefill !== null ? prefill : codeFromLink();
  var invited = !!code;
  var input = el("input", {type:"text", id:"code", "class":"code-in", value:code,
    autocomplete:"one-time-code", spellcheck:"false", "aria-describedby":"code-help",
    placeholder:"z. B. 7K4M-2QPD-9XTV"});

  function go(){
    var typed = (input.value||"").trim();
    if(!typed){
      S.err = plain("Bitte den Einladungscode eingeben."); S.err.refused = "no_code";
      return render();
    }
    S.pendingCode = typed; S.err = null; S.step = 2; render(); focusHeading();
  }

  show([
    head(invited ? "Ihre Einladung ist da." : "Einladung eingeben.",
         invited ? "Wir haben den Code aus Ihrem Link übernommen. Er steht nicht mehr in der " +
                   "Adresszeile, damit er beim Weitergeben des Links nicht mitwandert."
                 : "Geben Sie den Code ein, den Sie bekommen haben."),
    el("div", {"class":"card stack"}, [
      S.err ? problem(S.err, function(){ S.err=null; render(); }) : null,
      el("form", {onsubmit:function(ev){ ev.preventDefault(); go(); }}, [
        el("label", {"class":"f", "for":"code", text:"Einladungscode"}),
        input,
        el("p", {"class":"faint", id:"code-help",
          text:"Der Code gilt einmalig und läuft nach kurzer Zeit ab. Ein Tippfehler " +
               "verbraucht ihn — dann brauchen Sie einen neuen."}),
        el("div", {"class":"row"}, [
          el("button", {"class":"b primary", type:"submit", text:"Weiter"}),
          el("button", {"class":"b quiet", type:"button", text:"Zurück",
            onclick:function(){ S.step=0; S.err=null; render(); }})
        ])
      ])
    ])
  ]);
  if(!invited) input.focus();
}
SAYS.no_code = ["Bitte den Einladungscode eingeben.", "Er steht in Ihrer Einladung.", ""];

/* --- step 3: gerät benennen ----------------------------------------------- */

function stepName(){
  var input = el("input", {type:"text", id:"devname", value: S.deviceName || guessDeviceName(),
    autocomplete:"off", maxlength:"60"});
  var busy = false;

  function go(){
    if(busy) return;
    busy = true;
    var name = (input.value||"").trim() || guessDeviceName();
    S.deviceName = name; S.err = null;
    announce("Wird verbunden.");
    fetch("/v1/session", {method:"POST", headers:{"Content-Type":"application/json"},
      cache:"no-store", credentials:"same-origin", referrerPolicy:"no-referrer",
      body: JSON.stringify({code: S.pendingCode, client_name: name})
    }).then(function(r){
      return r.json().catch(function(){ return {}; }).then(function(body){
        if(!r.ok || !body.csrf) throw pairingProblem(body);
        return body;
      });
    }, function(){ throw plain("Die Sandbox ist gerade nicht erreichbar."); })
    .then(function(body){
      S.pendingCode = null;          // used once, and gone from this page too
      S.csrf = body.csrf;            // in memory only. Never stored, never in a cookie.
      S.device = body.device; S.deviceName = name;
      return call("capabilities");
    }).then(function(caps){
      S.caps = caps; S.step = 3; render(); focusHeading();
    }).catch(function(e){
      busy = false; S.err = e; S.step = e.backToCode ? 1 : 2; render();
    });
  }

  show([
    head("Wie soll dieses Gerät heißen?",
         "Der Name taucht später in Ihrer Liste auf. So erkennen Sie, was Sie zurückziehen, " +
         "wenn Sie ein Gerät verlieren."),
    el("div", {"class":"card stack"}, [
      S.err ? problem(S.err, null) : null,
      el("form", {onsubmit:function(ev){ ev.preventDefault(); go(); }}, [
        el("label", {"class":"f", "for":"devname", text:"Gerätename"}),
        input,
        el("p", {"class":"faint",
          text:"Vorschlag schon eingetragen — Sie können ihn einfach übernehmen."}),
        el("div", {"class":"row"}, [
          el("button", {"class":"b primary", type:"submit", id:"confirm-name",
            text:"Verbinden"}),
          el("button", {"class":"b quiet", type:"button", text:"Zurück",
            onclick:function(){ S.step=1; S.err=null; render(); }})
        ])
      ])
    ])
  ]);
}

/* The pairing route answers with the operator's own sentence, which names
 * commands to run on the server. Somebody using this page cannot run those, so
 * it is translated into something they CAN act on. */
function pairingProblem(body){
  var said = String((body && body.error) || "");
  var e;
  if(/expired/i.test(said)) e = named("invitation_expired");
  else if(/a pairing code is|characters/i.test(said)) e = named("invitation_short");
  else if(/does not match|invalid/i.test(said)) e = named("invitation_wrong");
  else if(/not accepting pairings/i.test(said)) e = named("invitation_used");
  else if(/too many|throttl/i.test(said)) e = named("invitation_throttled");
  else e = named("invitation_other");
  e.backToCode = true;
  return e;
}
function named(kind){ var e = plain(SAYS[kind][0]); e.refused = kind; return e; }

SAYS.invitation_expired = ["Diese Einladung ist abgelaufen.",
  "Einladungen gelten absichtlich nur kurz. Bitte eine neue anfordern.", ""];
SAYS.invitation_wrong = ["Dieser Code stimmt nicht.",
  "Jede Einladung erlaubt genau einen Versuch, also ist sie jetzt verbraucht. Bitte eine neue " +
  "anfordern.", ""];
SAYS.invitation_short = ["Dieser Code ist nicht vollständig.",
  "Ein Einladungscode hat zwölf Zeichen in drei Vierergruppen, zum Beispiel ABCD-EFGH-JKLM.", ""];
SAYS.invitation_used = ["Diese Einladung wurde schon benutzt.",
  "Jede Einladung funktioniert genau einmal. Bitte eine neue anfordern.", ""];
SAYS.invitation_throttled = ["Zu viele Versuche.",
  "Bitte einen Moment warten und es dann mit einer neuen Einladung versuchen.", ""];
SAYS.invitation_other = ["Die Einladung konnte nicht eingelöst werden.",
  "Bitte eine neue Einladung anfordern.", ""];

/* --- step 4: verbindung und schutz ---------------------------------------- */

function stepConfirm(){
  var caps = S.caps || {};
  var e = caps.enforces || {};
  var on = [], off = [];
  var READS = {
    network: ["Kein Zugang ins Netz, außer er wird ausdrücklich erlaubt",
              "Netzzugang wird hier nicht abgeschirmt"],
    memory: ["Arbeitsspeicher ist gedeckelt", "Arbeitsspeicher ist nicht gedeckelt"],
    wall_clock: ["Läuft nach der vereinbarten Zeit ab", "Laufzeit wird nicht erzwungen"],
    filesystem: ["Kein Zugriff auf Ihre Dateien", "Dateizugriff ist nicht abgeschirmt"],
    cpu: ["Rechenzeit ist gedeckelt", "Rechenzeit ist nicht gedeckelt"]
  };
  Object.keys(READS).forEach(function(k){
    if(!(k in e)) return;
    (e[k] ? on : off).push(READS[k][e[k] ? 0 : 1]);
  });

  show([
    head("Verbunden. Das ist der Schutz.",
         "Dieses Gerät heißt jetzt „" + (S.deviceName||"") + "“ und darf mit der Sandbox sprechen."),
    el("div", {"class":"card stack"}, [
      el("div", {"class":"note " + (caps.accepting_work === false ? "n-warn" : "n-good")}, [
        el("h3", {text: caps.accepting_work === false
          ? "Die Sandbox nimmt gerade keine Arbeit an." : "Die Verbindung steht."}),
        el("p", {text: caps.accepting_work === false
          ? "Einrichten können Sie trotzdem — Aufträge starten erst wieder, wenn der Betrieb läuft."
          : "Ab hier läuft alles über diese eine geprüfte Verbindung."})
      ]),
      on.length ? el("div", {}, [
        el("h2", {text:"Was gemessen abgeschirmt wird"}),
        el("ul", {}, on.map(function(t){ return el("li", {text:t}); }))
      ]) : null,
      off.length ? el("div", {}, [
        el("h2", {text:"Was hier nicht abgeschirmt wird"}),
        el("p", {"class":"muted",
          text:"Ausdrücklich genannt, damit niemand mehr annimmt, als da ist."}),
        el("ul", {}, off.map(function(t){ return el("li", {text:t}); }))
      ]) : null,
      el("div", {"class":"note n-plain"}, [
        el("p", {text:"Diese Sandbox läuft zum Entwickeln auf einem einzelnen Rechner. Sie ist " +
                      "kein Mehrkunden-Betrieb und kein Schutz gegen jemanden, der gezielt " +
                      "ausbrechen will."})
      ]),
      el("div", {"class":"row"}, [
        el("button", {"class":"b primary", type:"button", id:"confirm-protection",
          text:"Verstanden, weiter",
          onclick:function(){ S.step=4; render(); focusHeading(); }})
      ])
    ])
  ]);
}

/* --- step 5: KI auswählen ------------------------------------------------- */

function stepChoose(){
  var ops = ((S.caps && S.caps.operations) || []).map(function(o){ return o.name; });
  var canRun = ops.indexOf("submit") >= 0;
  var picks = el("div", {"class":"pick"});
  WAYS.forEach(function(w){
    if(w.channel && !canRun) return;
    picks.appendChild(el("button", {type:"button", id:"way-"+w.id,
      "aria-pressed": S.way === w.id ? "true" : "false",
      onclick:function(){ S.way = w.id; render(); }}, [
        el("b", {text:w.label}), el("span", {text:w.hint})
      ]));
  });

  var after = null;
  if(S.way === "none"){
    after = el("div", {"class":"note n-bad", id:"not-compatible", role:"note"}, [
      el("h3", {text:"Diese KI kann AgentNode nicht direkt verwenden, weil sie keine externen " +
                     "Werkzeuge aufrufen kann."}),
      el("p", {text:"Das ist keine Einstellungssache und lässt sich hier nicht umgehen: Was " +
                    "nichts außerhalb des Chats aufrufen kann, kann auch keine Sandbox benutzen."}),
      el("p", {text:"Diese Seite ist kein Ersatz dafür. Sie können hier selbst Aufträge starten " +
                    "und Ergebnisse ansehen — aber Ihre KI bleibt davon getrennt."}),
      el("button", {"class":"b ghost", type:"button", id:"use-console-anyway",
        text:"Trotzdem selbst weitermachen",
        onclick:function(){ S.way = "self"; S.step = 8; render(); focusHeading(); }})
    ]);
  } else if(S.way){
    after = el("div", {"class":"row"}, [
      el("button", {"class":"b primary", type:"button", id:"way-next", text:"Weiter",
        onclick:startSetup})
    ]);
  }

  show([
    head("Womit soll die Sandbox arbeiten?",
         "Wir richten die Verbindung passend ein. Wählen Sie, was am ehesten zutrifft."),
    el("div", {"class":"card stack"}, [picks, after])
  ]);
}

/* --- step 6: einrichtung -------------------------------------------------- */

function startSetup(){
  var chosen = WAYS.filter(function(w){ return w.id === S.way; })[0];
  S.err = null;
  announce("Einrichtung wird vorbereitet.");
  call("connections.enrol", {way_in: chosen.channel, label: chosen.label})
    .then(function(begun){
      S.setup = {challenge: begun.challenge, ticket: begun.ticket, label: chosen.label,
                 channel: chosen.channel};
      S.step = 5; render(); focusHeading();
    }).catch(function(e){ S.err = e; render(); });
}

function stepSetup(){
  /* The file is collected by a form POST, not a link. A link would put the
   * ticket in the address bar, the history and the referrer -- and the answer
   * streams back as a download, so the credential inside it never becomes a
   * value this page holds. */
  var form = el("form", {method:"POST", action:"/console/setup", id:"setup-form"}, [
    el("input", {type:"hidden", name:"challenge", value:S.setup.challenge}),
    el("input", {type:"hidden", name:"ticket", value:S.setup.ticket}),
    el("input", {type:"hidden", name:"confirm", value:S.csrf}),
    el("button", {"class":"b primary", type:"submit", id:"download-setup",
      text:"Einrichtungsdatei herunterladen"})
  ]);

  show([
    head("Ihre Einrichtung ist fertig.",
         "Sie müssen nichts selbst zusammenstellen und nichts von Hand ändern."),
    el("div", {"class":"card stack"}, [
      el("p", {text:"Übernehmen Sie diese eine Datei in „" + S.setup.label + "“. Mehr ist " +
                    "nicht nötig."}),
      form,
      el("div", {"class":"note n-warn"}, [
        el("p", {text:"Die Datei enthält Ihren Zugang. Behandeln Sie sie wie ein Passwort — " +
                      "nicht in einen Chat einfügen, nicht in ein geteiltes Verzeichnis legen."}),
        el("p", {text:"Sie wird genau einmal ausgegeben und ist in dieser Seite nirgends " +
                      "sichtbar."})
      ]),
      el("div", {"class":"row"}, [
        el("button", {"class":"b primary", type:"button", id:"setup-next",
          text:"Übernommen — jetzt testen",
          onclick:function(){ S.step=6; render(); focusHeading(); }}),
        el("button", {"class":"b quiet", type:"button", text:"Zurück",
          onclick:function(){ S.step=4; render(); }})
      ])
    ])
  ]);
}

/* --- step 7: testaufruf --------------------------------------------------- */

function stepTest(){
  var box = el("div", {"class":"stack", id:"test-area"});
  var stop = false;
  var mine = S.epoch;

  function look(tries){
    if(stop || S.epoch !== mine || S.step !== 6) return;
    call("connections.check", {challenge: S.setup.challenge}).then(function(said){
      if(said.satisfied){ S.step = 7; render(); focusHeading(); return; }
      if(tries > 300){
        S.err = named("no_call_arrived");
        box.innerHTML = ""; box.appendChild(problem(S.err, function(){ S.err=null; render(); }));
        return;
      }
      later(function(){ look(tries+1); }, 2000);
    }).catch(function(e){
      box.innerHTML = ""; box.appendChild(problem(e, function(){ render(); }));
    });
  }

  box.appendChild(el("p", {}, [el("span", {"class":"spin"}),
    document.createTextNode(" Wartet auf den ersten Aufruf aus „" + S.setup.label + "“…")]));
  box.appendChild(el("p", {"class":"faint",
    text:"Als funktionierend gilt die Verbindung erst, wenn die Sandbox einen echten Aufruf " +
         "genau dieser Verbindung verzeichnet hat. Vorher behaupten wir nichts."}));

  show([
    head("Jetzt prüfen wir, ob es wirklich funktioniert.",
         "Lassen Sie „" + S.setup.label + "“ einmal etwas in der Sandbox tun. Wir sagen " +
         "Bescheid, sobald der Aufruf hier angekommen ist."),
    el("div", {"class":"card stack"}, [box])
  ]);
  announce("Wartet auf den ersten Aufruf.");
  look(0);
}
SAYS.no_call_arrived = ["Es kam kein Aufruf an.",
  "Meist ist die Einrichtungsdatei noch nicht übernommen oder das Programm wurde noch nicht " +
  "neu gestartet.", "retry"];

/* --- step 8: bestätigung -------------------------------------------------- */

function stepWorks(){
  announce("Die Verbindung funktioniert.");
  show([
    head("Die Verbindung funktioniert.",
         "Das ist keine Vermutung: die Sandbox hat den Aufruf dieser Verbindung selbst " +
         "verzeichnet."),
    el("div", {"class":"card stack"}, [
      el("div", {"class":"note n-good"}, [
        el("h3", {text:"„" + (S.setup ? S.setup.label : "Ihr Programm") + "“ hat die Sandbox " +
                       "erreicht."}),
        el("p", {text:"Ab jetzt läuft Code an einem abgeschirmten Platz statt auf Ihrem Rechner."})
      ]),
      el("div", {"class":"row"}, [
        el("button", {"class":"b primary", type:"button", id:"to-first-job",
          text:"Ersten Auftrag starten",
          onclick:function(){ S.step=8; render(); focusHeading(); }}),
        el("button", {"class":"b quiet", type:"button", text:"Direkt zur Übersicht",
          onclick:function(){ S.step=9; S.tab="setup"; render(); focusHeading(); }})
      ])
    ])
  ]);
}

/* --- jobs ----------------------------------------------------------------- */

var CODE = "print('Hallo aus der Sandbox')";

function runJob(label){
  var run_id = hex(16);
  return digest(CODE).then(function(sha){
    return call("prepare", {command:["python","-c",CODE], artifact_sha256:sha,
                            artifact_bytes:new TextEncoder().encode(CODE).length,
                            wall_clock_s:30});
  }).then(function(told){
    return call("submit", {run_id:run_id, artifact:b64(CODE), command:["python","-c",CODE],
                           wall_clock_s:30, accepted_disclosure:told.accepted_disclosure});
  }).then(function(started){
    var job = {run_id: started.run_id, label: label || "Auftrag", state: started.state};
    S.jobs.unshift(job);
    watch(job);
    return job;
  });
}

/* Polls until the run reaches a state it will not leave. `stopping` is not one
 * of those: a cancellation is finished when the sandbox has been confirmed
 * gone, not when the record changed. */
var OVER = ["finished","refused","cancelled","unverified","interrupted"];
function watch(job){
  var tries = 0;
  var mine = S.epoch;
  (function look(){
    if(S.epoch !== mine) return;          // this page has been signed out from under us
    call("status", {run_id: job.run_id}).then(function(where){
      var was = job.state;
      job.state = where.state;
      if(was !== job.state) paintJobs();
      if(OVER.indexOf(where.state) >= 0){
        announce("Auftrag " + job.label + ": " + (STATE_WORDS[where.state]||[where.state])[0]);
        return call("result", {run_id: job.run_id}).then(function(out){
          job.out = out; paintJobs();
        }, function(){ paintJobs(); });
      }
      if(++tries > 600) return;
      later(look, 1000);
    }, function(){ if(++tries <= 600) later(look, 2000); });
  })();
}

function cancelJob(job){
  job.cancelling = true; paintJobs();
  announce("Abbruch wird ausgeführt.");
  call("cancel", {run_id: job.run_id}).then(function(said){
    job.state = said.state;                    // "stopping" -- and this page does not block on it
    paintJobs();
  }).catch(function(e){ job.cancelling = false; job.err = e; paintJobs(); });
}

var STATE_WORDS = {
  accepted:["angenommen","p-run"], running:["läuft","p-run"],
  stopping:["Abbruch wird ausgeführt","p-warn"],
  finished:["fertig","p-good"], refused:["abgelehnt","p-bad"],
  cancelled:["abgebrochen","p-warn"], unverified:["ohne Bestätigung beendet","p-warn"],
  interrupted:["unterbrochen","p-warn"]
};

function jobCard(job){
  var w = STATE_WORDS[job.state] || [job.state, "p-run"];
  var running = ["accepted","running","stopping"].indexOf(job.state) >= 0;
  var body = [];
  if(job.out && job.out.stdout) body.push(el("pre", {"class":"out", text: job.out.stdout}));
  if(job.out && job.out.stderr) body.push(el("pre", {"class":"out", text: job.out.stderr}));
  if(job.state === "stopping")
    body.push(el("p", {"class":"faint",
      text:"Der Abbruch ist angefordert. Fertig heißt er erst, wenn die Sandbox bestätigt hat, " +
           "dass wirklich nichts mehr läuft."}));
  if(job.out && job.out.cleanup_verified === false)
    body.push(el("p", {"class":"faint",
      text:"Die Sandbox konnte nicht bestätigen, dass alles abgeräumt ist. Das wird hier gesagt " +
           "statt stillschweigend als erledigt gezählt."}));
  if(job.err) body.push(problem(job.err, null));

  return el("li", {"data-run": job.run_id}, [
    el("div", {"class":"who"}, [
      el("b", {text: job.label}),
      el("span", {"class":"faint", text:"Auftrag " + job.run_id.slice(0,8)})
    ]),
    el("div", {"class":"row"}, [
      el("span", {"class":"pill " + w[1], text: w[0]}),
      running && !job.cancelling
        ? el("button", {"class":"b danger", type:"button", "data-cancel": job.run_id,
            text:"Abbrechen", onclick:function(){ cancelJob(job); }})
        : null
    ]),
    body.length ? el("div", {style:"flex-basis:100%"}, body) : null
  ]);
}

function paintJobs(){
  var host = document.getElementById("joblist");
  if(!host) return;
  host.innerHTML = "";
  if(!S.jobs.length){
    host.appendChild(el("p", {"class":"muted", text:"Noch keine Aufträge."}));
    return;
  }
  var ul = el("ul", {"class":"list"});
  S.jobs.forEach(function(j){ ul.appendChild(jobCard(j)); });
  host.appendChild(ul);
}

function stepFirstJob(){
  function start(){
    S.err = null;
    var btn = document.getElementById("start-job");
    if(btn){ btn.disabled = true; btn.textContent = "Wird gestartet…"; }
    announce("Auftrag wird gestartet.");
    runJob("Mein erster Auftrag").then(function(){
      S.step = 9; S.tab = "setup"; render(); focusHeading();
    }).catch(function(e){ S.err = e; render(); });
  }
  show([
    head("Starten wir etwas Echtes.",
         "Ein kleiner Auftrag, damit Sie einmal den ganzen Weg sehen: starten, zusehen, Ergebnis."),
    el("div", {"class":"card stack"}, [
      S.err ? problem(S.err, function(){ S.err=null; render(); }) : null,
      el("p", {text:"Der Auftrag gibt einen kurzen Text aus. Er läuft in der Sandbox, nicht hier."}),
      el("div", {"class":"row"}, [
        el("button", {"class":"b primary", type:"button", id:"start-job", text:"Auftrag starten",
          onclick:start}),
        el("button", {"class":"b quiet", type:"button", text:"Überspringen",
          onclick:function(){ S.step=9; render(); focusHeading(); }})
      ])
    ])
  ]);
}

/* --- the customer area ----------------------------------------------------- */

function overview(){
  var jobs = el("div", {id:"joblist"});
  var usage = el("div", {id:"usagebox"});
  var switchbox = el("div", {id:"switchbox"});

  show([
    el("div", {}, [
      el("h1", {text:"Ihre Sandbox"}),
      el("p", {"class":"lede",
        text:"Was gerade läuft, was gelaufen ist, und wie viel noch übrig ist."})
    ]),
    el("div", {"class":"card stack"}, [switchbox]),
    el("div", {"class":"card stack"}, [
      el("div", {"class":"spread"}, [
        el("h2", {text:"Aufträge"}),
        el("button", {"class":"b ghost", type:"button", id:"new-job", text:"Neuen Auftrag starten",
          onclick:function(){
            runJob("Auftrag").catch(function(e){ S.err = e; render(); });
            paintJobs();
          }})
      ]),
      jobs
    ]),
    el("div", {"class":"card stack"}, [el("h2", {text:"Verbrauch"}), usage])
  ]);
  paintJobs(); paintUsage(usage); paintSwitch(switchbox);
}

function paintSwitch(host){
  call("capabilities").then(function(caps){
    S.caps = caps; drawFoot();
    host.innerHTML = "";
    var off = caps.accepting_work === false;
    host.appendChild(el("div", {"class":"note " + (off ? "n-warn" : "n-good"), id:"killswitch"}, [
      el("h3", {text: off ? "Der Not-Aus ist gezogen." : "Die Sandbox nimmt Arbeit an."}),
      el("p", {text: off
        ? "Es werden gerade keine neuen Aufträge angenommen. Das ist eine bewusste Entscheidung " +
          "der Person, die die Sandbox betreibt — nicht ein Fehler."
        : "Neue Aufträge sind möglich. Wer die Sandbox betreibt, kann das jederzeit anhalten."})
    ]));
  }).catch(function(e){
    host.innerHTML = ""; host.appendChild(problem(e, function(){ render(); }));
  });
}

function paintUsage(host){
  host.innerHTML = "";
  host.appendChild(el("p", {}, [el("span", {"class":"spin"}), document.createTextNode(" Lädt…")]));
  call("usage", {}).then(function(u){
    host.innerHTML = "";
    var ceil = u.ceilings || {};
    var maxRuns = ceil.runs_per_window || 0, maxSecs = ceil.seconds_per_window || 0;
    host.appendChild(bar("Aufträge in diesem Zeitfenster", u.runs || 0, maxRuns, ""));
    host.appendChild(bar("Laufzeit in diesem Zeitfenster", u.seconds || 0, maxSecs, " Sek."));
    if(!maxRuns && !maxSecs)
      host.appendChild(el("p", {"class":"faint",
        text:"Für dieses Gerät ist keine Obergrenze gesetzt."}));
  }).catch(function(e){
    host.innerHTML=""; host.appendChild(problem(e, function(){ paintUsage(host); }));
  });
}

function bar(label, used, max, unit){
  var frac = max ? Math.min(1, used/max) : 0;
  var cls = frac >= 1 ? "over" : (frac > .8 ? "hot" : "");
  return el("div", {"class":"nums"}, [
    el("div", {"class":"spread"}, [
      el("span", {text:label}),
      el("span", {"class":"muted", text: max ? (used + " von " + max + unit) : (used + unit)})
    ]),
    max ? el("div", {"class":"meter"}, [el("i", {"class":cls, style:"width:"+(frac*100)+"%"})])
        : null,
    max && frac >= 1 ? el("p", {"class":"faint",
      text:"Aufgebraucht. Laufende Aufträge laufen weiter; neue gehen erst wieder, wenn das " +
           "Fenster weiterrückt."}) : null
  ]);
}

function devices(){
  var host = el("div", {id:"devicelist"});
  show([
    el("div", {}, [
      el("h1", {text:"Verbundene Geräte und KIs"}),
      el("p", {"class":"lede", text:"Alles, was gerade als Sie mit der Sandbox sprechen darf. " +
        "Zurückziehen wirkt sofort und überall."})
    ]),
    el("div", {"class":"card"}, [host])
  ]);
  loadDevices(host);
}

function loadDevices(host){
  host.innerHTML = "";
  host.appendChild(el("p", {}, [el("span", {"class":"spin"}), document.createTextNode(" Lädt…")]));
  Promise.all([call("devices.list"), call("devices.invitations")]).then(function(both){
    var d = both[0], open = (both[1] && both[1].invitations) || [];
    host.innerHTML = "";
    var ul = el("ul", {"class":"list"});
    (d.devices||[]).forEach(function(dev){
      ul.appendChild(el("li", {"data-device": dev.device_id}, [
        el("div", {"class":"who"}, [
          el("b", {text: dev.name || "Unbenanntes Gerät"}),
          el("span", {"class":"faint", text: ago(dev.last_used)})
        ]),
        el("button", {"class":"b danger", type:"button", "data-revoke": dev.device_id,
          text:"Zugang zurückziehen", onclick:function(){ revoke(dev, host); }})
      ]));
    });
    host.appendChild(ul);

    /* Adding the next machine. One button, then a code and the one line to run with it.
     * Nothing here asks for an account: which account this joins is decided by the sandbox
     * from the session making the request, so there is no field to get wrong. */
    host.appendChild(el("h3", {"class":"sub", text:"Weiteres Gerät hinzufügen"}));
    host.appendChild(el("p", {"class":"lede",
      text:"Damit erreicht ein zweiter Rechner — oder ein weiterer Zugang — diese Sandbox als " +
           "Sie. Die Einladung gilt 30 Minuten und funktioniert genau einmal."}));
    host.appendChild(el("button", {"class":"b", type:"button", id:"invite-device",
      text:"Einladung erstellen", onclick:function(){ inviteADevice(host); }}));

    if(open.length){
      host.appendChild(el("h3", {"class":"sub", text:"Offene Einladungen"}));
      var ol = el("ul", {"class":"list"});
      open.forEach(function(inv){
        ol.appendChild(el("li", {"data-invitation": inv.invitation}, [
          el("div", {"class":"who"}, [
            el("b", {text: inv.label || "Ohne Namen"}),
            el("span", {"class":"faint", text:"läuft ab " + when(inv.expires_at)})
          ]),
          el("button", {"class":"b quiet", type:"button", text:"Zurückziehen",
            onclick:function(){
              call("devices.uninvite", {invitation: inv.invitation}).then(function(){
                announce("Einladung zurückgezogen."); loadDevices(host);
              }).catch(function(e){ host.appendChild(problem(e, null)); });
            }})
        ]));
      });
      host.appendChild(ol);
    }
  }).catch(function(e){
    host.innerHTML=""; host.appendChild(problem(e, function(){ loadDevices(host); }));
  });
}

/* The code is shown ONCE, here, because the sandbox never stores it and cannot show it again.
 * That is said on the screen rather than left for somebody to discover after closing it. */
function inviteADevice(host){
  call("devices.invite", {label: ""}).then(function(made){
    var box = el("div", {"class":"note n-good", id:"the-invitation"}, [
      el("h3", {text:"Das hier einmal am anderen Rechner eingeben."}),
      el("p", {"class":"code", id:"invitation-code", text: made.code}),
      el("p", {text:"Oder dort direkt diesen Befehl ausführen:"}),
      el("p", {"class":"code", id:"invitation-command", text: made.what_to_do}),
      el("p", {"class":"said-by",
        text:"Wird nur jetzt angezeigt. Diese Sandbox speichert die Einladung nicht im " +
             "Klartext und kann sie nicht noch einmal zeigen — wenn sie weg ist, erstellen " +
             "Sie einfach eine neue."}),
      el("button", {"class":"b ghost", type:"button", id:"invitation-done", text:"Fertig",
        onclick:function(){ loadDevices(host); }})
    ]);
    host.insertBefore(box, host.firstChild);
    announce("Einladung erstellt.");
  }).catch(function(e){ host.appendChild(problem(e, null)); });
}

function when(at){
  if(!at) return "";
  try { return new Date(at * 1000).toLocaleTimeString(); } catch(e) { return ""; }
}

function revoke(dev, host){
  var mine = dev.device_id === S.device;
  host.innerHTML = "";
  host.appendChild(el("div", {"class":"note n-warn", id:"revoke-confirm"}, [
    el("h3", {text:"„" + (dev.name||"Dieses Gerät") + "“ wirklich zurückziehen?"}),
    el("p", {text: mine
      ? "Das ist dieses Gerät. Danach werden Sie hier abgemeldet und brauchen eine neue Einladung."
      : "Danach funktioniert nichts mehr, was dieses Gerät gespeichert hat — sofort, auf allen " +
        "Wegen."}),
    el("div", {"class":"row"}, [
      el("button", {"class":"b danger", type:"button", id:"revoke-yes", text:"Ja, zurückziehen",
        onclick:function(){
          call("devices.revoke", {device_id: dev.device_id}).then(function(){
            announce("Zugang zurückgezogen.");
            if(mine) signedOut(); else loadDevices(host);
          }).catch(function(e){ host.appendChild(problem(e, null)); });
        }}),
      el("button", {"class":"b quiet", type:"button", text:"Abbrechen",
        onclick:function(){ loadDevices(host); }})
    ])
  ]));
}

function sessions(){
  var host = el("div", {id:"sessionlist"});
  show([
    el("div", {}, [
      el("h1", {text:"Ihre Anmeldungen"}),
      el("p", {"class":"lede",
        text:"Browser, in denen Sie gerade angemeldet sind. Beenden wirkt sofort."})
    ]),
    el("div", {"class":"card"}, [host])
  ]);
  loadSessions(host);
}

function loadSessions(host){
  host.innerHTML = "";
  host.appendChild(el("p", {}, [el("span", {"class":"spin"}), document.createTextNode(" Lädt…")]));
  call("sessions.list").then(function(d){
    host.innerHTML = "";
    var ul = el("ul", {"class":"list"});
    (d.sessions||[]).forEach(function(sess){
      ul.appendChild(el("li", {"data-session": sess.session}, [
        el("div", {"class":"who"}, [
          el("b", {text: sess.label || "Ein Browser"}),
          el("span", {"class":"faint", text: "zuletzt benutzt " + ago(sess.last_used)})
        ]),
        el("button", {"class":"b danger", type:"button", "data-end": sess.session,
          text:"Beenden",
          onclick:function(){
            call("sessions.end", {session: sess.session}).then(function(said){
              announce("Anmeldung beendet.");
              if(said.this_one) signedOut(); else loadSessions(host);
            }).catch(function(e){ host.appendChild(problem(e, null)); });
          }})
      ]));
    });
    host.appendChild(ul);
    host.appendChild(el("div", {"class":"row"}, [
      el("button", {"class":"b ghost", type:"button", id:"sign-out", text:"Hier abmelden",
        onclick:function(){
          call("sessions.end", {}).then(signedOut).catch(function(){ signedOut(); });
        }})
    ]));
  }).catch(function(e){
    host.innerHTML=""; host.appendChild(problem(e, function(){ loadSessions(host); }));
  });
}

function safety(){
  var caps = S.caps || {};
  var e = caps.enforces || {};
  var WORDS = {network:"Netzzugang", memory:"Arbeitsspeicher", cpu:"Rechenzeit",
               wall_clock:"Laufzeit", filesystem:"Ihre Dateien"};
  var rows = Object.keys(e).map(function(k){
    return el("li", {}, [
      el("div", {"class":"who"}, [el("b", {text: WORDS[k] || k})]),
      el("span", {"class":"pill " + (e[k] ? "p-good" : "p-warn"),
        text: e[k] ? "abgeschirmt" : "nicht abgeschirmt"})
    ]);
  });
  show([
    el("div", {}, [
      el("h1", {text:"Sicherheit und Netz"}),
      el("p", {"class":"lede", text:"In klaren Worten, ohne Beschönigung."})
    ]),
    el("div", {"class":"card stack"}, [
      el("h2", {text:"Was gemessen wurde"}),
      rows.length ? el("ul", {"class":"list"}, rows)
                  : el("p", {"class":"muted", text:"Diese Sandbox meldet dazu nichts."})
    ]),
    el("div", {"class":"card stack"}, [
      el("h2", {text:"Was diese Sandbox nicht ist"}),
      el("ul", {}, (caps.what_this_does_not_establish||[]).map(function(l){
        return el("li", {text:l}); }))
    ]),
    el("div", {"class":"card stack"}, [
      el("h2", {text:"Ihr Zugang in diesem Browser"}),
      el("p", {text:"Ihr Zugang liegt nicht in dieser Seite. Er steckt in einem Cookie, das " +
                    "diese Seite selbst nicht lesen kann, und gilt nur für diese Adresse. " +
                    "Deshalb bleiben Sie über einen Neustart des Browsers angemeldet, ohne " +
                    "dass hier ein Schlüssel herumliegt."}),
      el("p", {text:"Sie können jede Anmeldung einzeln beenden. Das wirkt sofort."}),
      el("div", {"class":"row"}, [
        el("button", {"class":"b danger", type:"button", id:"logout", text:"Hier abmelden",
          onclick:function(){
            call("sessions.end", {}).then(signedOut).catch(function(){ signedOut(); });
          }})
      ])
    ])
  ]);
}

function signedOut(){
  // First, because everything below is state those pollers are holding.
  stopEverythingScheduled();
  S.csrf = ""; S.device = ""; S.caps = null; S.jobs = []; S.setup = null;
  S.step = 0; S.tab = "setup"; S.err = null; S.way = null;
  announce("Abgemeldet.");
  render(); focusHeading();
}

/* --- the router ------------------------------------------------------------ */

function render(){
  if(!S.csrf){
    if(S.step === 1) return stepInvitation(S.pendingCode);
    if(S.step === 2) return stepName();
    S.step = 0; return stepWelcome();
  }
  if(S.tab === "devices") return devices();
  if(S.tab === "sessions") return sessions();
  if(S.tab === "usage"){
    var host = el("div", {id:"usagebox"});
    show([el("div", {}, [el("h1", {text:"Verbrauch"}),
      el("p", {"class":"lede", text:"Was dieses Zeitfenster schon gekostet hat."})]),
      el("div", {"class":"card"}, [host])]);
    return paintUsage(host);
  }
  if(S.tab === "safety") return safety();
  if(S.step === 3) return stepConfirm();
  if(S.step === 4) return stepChoose();
  if(S.step === 5) return stepSetup();
  if(S.step === 6) return stepTest();
  if(S.step === 7) return stepWorks();
  if(S.step === 8) return stepFirstJob();
  return overview();
}

/* Coming back to a valid session must not mean doing the setup again. The
 * cookie is still there after a browser restart; what is gone is the
 * confirmation value, which lives only in this page's memory -- so it is
 * fetched once, and only then is somebody signed in. */
function boot(){
  var carried = codeFromLink();
  if(carried){ S.pendingCode = carried; S.step = 1; return stepInvitation(carried); }
  call("sessions.list").then(function(){
    return fetch("/console/confirm", {credentials:"same-origin", cache:"no-store"})
      .then(function(r){ return r.ok ? r.json() : null; });
  }).then(function(said){
    if(!said || !said.csrf) throw plain("nicht angemeldet");
    S.csrf = said.csrf; S.device = said.device || "";
    S.step = 9; S.tab = "setup";
    render();
    return call("capabilities").then(function(c){ S.caps = c; drawFoot(); });
  }).catch(function(){ S.step = 0; render(); });
}
document.addEventListener("DOMContentLoaded", boot);
