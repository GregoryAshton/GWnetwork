// Google Apps Script for the super-gold labelling site.
//
// Setup (once): create a Google Sheet, then Extensions > Apps Script, paste
// this file, Deploy > New deployment > Web app, "Execute as: Me", "Who has
// access: Anyone". Copy the /exec URL and pass it to
// `gwn eval supergold site --endpoint URL`.
//
// Names offered for the acknowledgements go to a separate sheet with no token
// and no timestamp, so they cannot be matched to answers.
//
// Each submission (one paper from one reviewer) is appended as a row:
// received_at, token, arxiv_id, payload (the raw JSON). Download the sheet as
// CSV (File > Download > .csv) and run `gwn eval supergold import FILE.csv`;
// the importer validates tokens, papers and answers.

const SHEET = "labels";
const ACK_SHEET = "acknowledgements";  // no timestamps, no tokens
const MAX_BYTES = 45000;  // a Sheets cell holds 50,000 characters

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const body = e.postData.contents;
    if (body.length > MAX_BYTES) throw new Error("payload too large");
    const p = JSON.parse(body);
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    // Acknowledgements go to their own sheet with no token, so names can
    // never be joined to answers.
    if (p.type === "ack") {
      const name = String(p.name || "").slice(0, 200).replace(/^[=+@-]+/, "");
      const aff = String(p.affiliation || "").slice(0, 200).replace(/^[=+@-]+/, "");
      if (!name.trim()) throw new Error("empty name");
      const ack = ss.getSheetByName(ACK_SHEET) || ss.insertSheet(ACK_SHEET);
      if (ack.getLastRow() === 0) ack.appendRow(["name", "affiliation"]);
      ack.appendRow([name, aff]);
      return json({ok: true});
    }
    if (!/^[0-9a-f]{8}$/.test(p.token || "")) throw new Error("bad token");
    if (typeof p.arxiv_id !== "string" || p.arxiv_id.length > 40) throw new Error("bad paper");
    const sh = ss.getSheetByName(SHEET) || ss.insertSheet(SHEET);
    if (sh.getLastRow() === 0) sh.appendRow(["received_at", "token", "arxiv_id", "payload"]);
    // The payload starts with "{", so Sheets never reads it as a formula.
    sh.appendRow([new Date().toISOString(), p.token, p.arxiv_id, body]);
    return json({ok: true});
  } catch (err) {
    return json({ok: false, error: String(err)});
  } finally {
    lock.releaseLock();
  }
}

function json(o) {
  return ContentService.createTextOutput(JSON.stringify(o))
    .setMimeType(ContentService.MimeType.JSON);
}
