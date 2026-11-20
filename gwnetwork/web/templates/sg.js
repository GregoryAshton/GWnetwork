// Super-gold labelling site. No server: the queue is a static JSON file per
// reviewer token, answers go to a Google Apps Script endpoint, and the
// browser keeps a copy of everything it has sent or failed to send.
(function () {
  "use strict";
  const CFG = window.SG_CONFIG || {};
  const ls = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
  };

  function token() {
    const r = new URLSearchParams(location.search).get("r");
    if (r && /^[0-9a-f]{8}$/.test(r)) { ls.set("sg_token", r); return r; }
    return ls.get("sg_token", null);
  }

  async function queue(tok, base) {
    const cached = ls.get("sg_queue_" + tok, null);
    if (cached) return cached;
    const res = await fetch(base + "q/" + tok + ".json", {cache: "no-store"});
    if (!res.ok) return null;
    const q = (await res.json()).queue;
    ls.set("sg_queue_" + tok, q);
    return q;
  }

  const doneKey = tok => "sg_done_" + tok;
  const isDone = (tok, id) => !!ls.get(doneKey(tok), {})[id];
  function markDone(tok, id) { const d = ls.get(doneKey(tok), {}); d[id] = true; ls.set(doneKey(tok), d); }
  const nextUndone = (tok, q) => q.find(p => !isDone(tok, p.arxiv_id)) || null;

  async function post(payload) {
    if (!CFG.endpoint) return true;   // test mode: kept in this browser only
    try {
      const res = await fetch(CFG.endpoint, {method: "POST", body: JSON.stringify(payload)});
      const j = await res.json();
      return !!j.ok;
    } catch (e) { return false; }
  }

  // Every payload is kept (for "download my answers"); unsent ones are retried.
  async function send(payload) {
    const all = ls.get("sg_sent", []); all.push(payload); ls.set("sg_sent", all);
    const ok = await post(payload);
    if (!ok) { const p = ls.get("sg_pending", []); p.push(payload); ls.set("sg_pending", p); }
    return ok;
  }

  async function retryPending() {
    const pending = ls.get("sg_pending", []);
    const left = [];
    for (const p of pending) if (!(await post(p))) left.push(p);
    ls.set("sg_pending", left);
    return left.length;
  }

  function showMode() {
    const el = document.getElementById("mode");
    if (el && !CFG.endpoint) { el.hidden = false; el.textContent = "test mode: answers stay in this browser"; }
  }

  function download() {
    const blob = new Blob([JSON.stringify(ls.get("sg_sent", []), null, 1)], {type: "application/json"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "gw-labels-" + (token() || "anon") + ".json";
    a.click();
  }

  // Names for the acknowledgements: no token, so never joinable to answers.
  // Not kept in "download my answers" either.
  function ackForm() {
    const $ = id => document.getElementById(id);
    if (ls.get("sg_ack_sent", false)) {
      $("ack-form").hidden = true;
      $("ack-status").textContent = "Your name has been added. Thank you!";
      return;
    }
    $("ack-send").addEventListener("click", async () => {
      const name = $("ack-name").value.trim();
      if (!name) { $("ack-status").textContent = "Please enter a name."; return; }
      $("ack-send").disabled = true;
      const ok = await post({type: "ack", name, affiliation: $("ack-aff").value.trim()});
      if (ok) {
        ls.set("sg_ack_sent", true);
        $("ack-form").hidden = true;
        $("ack-status").textContent = "Your name has been added. Thank you!";
      } else {
        $("ack-send").disabled = false;
        $("ack-status").textContent = "Couldn't send it; please try again later.";
      }
    });
  }

  // ---------------------------------------------------------------- index
  async function indexPage() {
    showMode();
    const tok = token();
    if (!tok) { document.getElementById("no-token").hidden = false; return; }
    const q = await queue(tok, "");
    if (!q) { document.getElementById("bad-token").hidden = false; return; }
    document.getElementById("main").hidden = false;
    const done = q.filter(p => isDone(tok, p.arxiv_id)).length;
    document.getElementById("done-n").textContent = done;
    document.getElementById("total-n").textContent = q.length;
    const nxt = nextUndone(tok, q);
    const go = document.getElementById("go");
    if (nxt) {
      go.href = "p/" + nxt.slug + ".html?r=" + tok;
      if (done) go.querySelector("button").textContent = "Continue";
    } else {
      go.hidden = true;
      document.getElementById("all-done").hidden = false;
    }
    document.getElementById("download").addEventListener("click", download);
    ackForm();
    const left = await retryPending();
    if (left) {
      const el = document.getElementById("pending");
      el.hidden = false;
      el.textContent = left + " answer(s) could not be sent yet; they will be retried. If this persists, download your answers and email the file.";
    }
  }

  // ---------------------------------------------------------------- paper
  function paperPage() {
    showMode();
    const P = window.SG_PAGE;
    const tok = token();
    const $ = id => document.getElementById(id);
    if (!tok) { location.href = "../index.html"; return; }
    document.querySelectorAll("a.keep-r").forEach(a => a.href += "?r=" + tok);
    if (isDone(tok, P.arxiv_id)) $("again").hidden = false;

    let q = null;
    queue(tok, "../").then(qq => {
      q = qq;
      if (!q) return;
      const pos = q.findIndex(p => p.arxiv_id === P.arxiv_id);
      const done = q.filter(p => isDone(tok, p.arxiv_id)).length;
      $("prog").textContent = (pos >= 0 ? "paper " + (pos + 1) + " of " + q.length + " · " : "") + done + " done";
      $("progbar").style.width = (100 * done / q.length).toFixed(1) + "%";
    });
    retryPending();

    // Time on the page while it is visible.
    let visibleMs = 0, since = document.hidden ? null : Date.now();
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && since !== null) { visibleMs += Date.now() - since; since = null; }
      else if (!document.hidden) since = Date.now();
    });
    const seconds = () => Math.round((visibleMs + (since !== null ? Date.now() - since : 0)) / 1000);

    let listed = null;
    if (P.uniform) {
      $("recall-done").addEventListener("click", () => {
        listed = $("listed").value.trim() || null;
        $("recall").hidden = true; $("pairs").hidden = false; activate(0);
      });
      $("recall-skip").addEventListener("click", () => {
        listed = null; $("recall").hidden = true; $("pairs").hidden = false; activate(0);
      });
    }

    const cards = [...document.querySelectorAll(".card")];
    let active = 0;
    function activate(i) {
      active = Math.max(0, Math.min(cards.length - 1, i));
      cards.forEach((c, j) => c.classList.toggle("active", j === active));
    }
    function sync(card) {
      const i = card.dataset.i;
      const any = [...card.querySelectorAll(`input[name="uses-${i}"]`)].some(x => x.checked);
      const fb = card.querySelector(".fallback");
      fb.classList.toggle("off", any);
      if (any) card.querySelectorAll(`input[name="fb-${i}"]`).forEach(x => x.checked = false);
      const conf = card.querySelector(`input[name="conf-${i}"]:checked`);
      card.querySelector(".which").hidden = !(conf && conf.value === "ambiguous");
      if (answer(card)) card.classList.remove("missing");
    }
    function answer(card) {
      const i = card.dataset.i;
      const uses = [...card.querySelectorAll(`input[name="uses-${i}"]:checked`)].map(x => x.value);
      const fb = card.querySelector(`input[name="fb-${i}"]:checked`);
      const conf = card.querySelector(`input[name="conf-${i}"]:checked`);
      if (!(uses.length || fb) || !conf) return null;
      return {event: card.dataset.event, uses: uses.length ? uses : [fb.value],
              confidence: conf.value, which: card.querySelector(".which").value.trim()};
    }
    cards.forEach((card, j) => {
      card.addEventListener("click", () => activate(j));
      card.addEventListener("focusin", () => activate(j));
      card.querySelectorAll("input").forEach(x => x.addEventListener("change", () => sync(card)));
      sync(card);
    });
    if (!P.uniform) activate(0);

    document.addEventListener("keydown", e => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target instanceof Element && e.target.matches("input[type=text], textarea, select")) return;
      if ($("pairs").hidden || !cards.length) return;
      const card = cards[active], i = card.dataset.i;
      const data = card.querySelectorAll(`input[name="uses-${i}"]`);
      const fb = card.querySelectorAll(`input[name="fb-${i}"]`);
      const conf = card.querySelectorAll(`input[name="conf-${i}"]`);
      const n = Number(e.key);
      if (n >= 1 && n <= data.length) data[n - 1].checked = !data[n - 1].checked;
      else if ((n === 4 || n === 5) && !card.querySelector(".fallback.off")) fb[n - 4].checked = true;
      else if (e.key === "c" || e.key === "p" || e.key === "a") conf["cpa".indexOf(e.key)].checked = true;
      else if (e.key === "n") { activate(active + 1); cards[active].scrollIntoView({block: "start", behavior: "smooth"}); }
      else return;
      e.preventDefault(); sync(card);
    });

    function payload(extra) {
      return Object.assign({
        v: 1, token: tok, arxiv_id: P.arxiv_id, kind: P.kind,
        seconds: seconds(), author: $("author").checked,
        comment: $("comment").value.trim(), events_listed: listed,
        site: CFG.site, guide: CFG.guide, gold_version: CFG.gold_version,
        submitted_at: new Date().toISOString(), skipped: null, pairs: [],
      }, extra);
    }

    function goNext() {
      const nxt = q && nextUndone(tok, q);
      location.href = nxt ? nxt.slug + ".html?r=" + tok : "../index.html?r=" + tok;
    }

    async function finish(p) {
      $("submit").disabled = $("skip").disabled = true;
      $("status").textContent = "Sending…";
      const ok = await send(p);
      markDone(tok, P.arxiv_id);
      $("status").textContent = ok ? "Saved." : "Couldn't reach the server; your answer is kept in this browser and will be sent later.";
      if (P.kind === "calibration" && !p.skipped) { showFeedback(p); return; }
      setTimeout(goNext, ok ? 300 : 1500);
    }

    const label = uses => uses.join(" + ").replace(/_/g, " ");
    function showFeedback(p) {
      p.pairs.forEach(a => {
        const card = cards.find(c => c.dataset.event === a.event);
        const ref = P.answers[a.event] || [];
        const refShown = ref.length ? ref : ["cited_only"];
        const same = JSON.stringify([...a.uses].sort()) === JSON.stringify([...refShown].sort());
        const box = card.querySelector(".feedback");
        box.hidden = false;
        box.className = "feedback " + (same ? "ok" : "diff");
        box.innerHTML = "";
        box.append(same ? "Matches the reference answer: " : "Reference answer: ",
                   Object.assign(document.createElement("b"), {textContent: label(refShown)}),
                   same ? "" : " (you: " + label(a.uses) + "). Compare with the guide's worked cases.");
      });
      $("continue").hidden = false;
      $("continue").addEventListener("click", goNext);
      cards[0].scrollIntoView({block: "start", behavior: "smooth"});
    }

    $("submit").addEventListener("click", () => {
      const answers = cards.map(answer);
      const missing = cards.filter((c, j) => !answers[j]);
      cards.forEach(c => c.classList.toggle("missing", missing.includes(c)));
      if ($("pairs").hidden) { $("err").hidden = false; $("err").textContent = "Finish or skip the first step (listing events) first."; return; }
      if (missing.length) {
        $("err").hidden = false;
        $("err").textContent = missing.length + " event(s) still need an answer and a confidence (outlined in red).";
        missing[0].scrollIntoView({block: "center", behavior: "smooth"});
        return;
      }
      $("err").hidden = true;
      finish(payload({pairs: answers}));
    });

    $("skip").addEventListener("click", () => {
      const reason = $("skip-reason").value;
      if (!reason) { $("err").hidden = false; $("err").textContent = "Choose a reason for skipping."; return; }
      finish(payload({skipped: reason}));
    });
  }

  window.SG = {indexPage, paperPage};
})();
