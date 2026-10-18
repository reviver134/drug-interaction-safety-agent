import { useEffect, useRef, useState } from "react";

const TYPES = ["image/jpeg", "image/png", "image/webp"];
const LIMIT = 8 * 1024 * 1024;
const SEVERITY_ORDER = {BLOCK: 0, FLAG: 1, NOTE: 2, INSUFFICIENT_EVIDENCE: 3, NO_KNOWN_INTERACTION: 4};
const newRow = () => ({id: crypto.randomUUID(), name: "", strength: "", formulation: "", uncertain: false, note: ""});
async function request(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Please check your input and try again.");
  return data;
}
const post = (path, body, signal) => request(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body), signal});

export default function App() {
  const [images, setImages] = useState([]), [rows, setRows] = useState([newRow()]);
  const [warnings, setWarnings] = useState([]), [matches, setMatches] = useState(null);
  const [confirmed, setConfirmed] = useState(false), [result, setResult] = useState(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState("");
  const [config, setConfig] = useState(null);
  const version = useRef(0), controller = useRef(null), imageRef = useRef([]);
  const upload = useRef(null);
  useEffect(() => {
    request("/api/config").then(setConfig).catch(() => {});
    return () => {controller.current?.abort(); imageRef.current.forEach(i => URL.revokeObjectURL(i.url));};
  }, []);
  function invalidate() {
    version.current++; controller.current?.abort(); setBusy("");
    setMatches(null); setConfirmed(false); setResult(null); setError("");
  }
  function updateImages(next) {
    invalidate(); setRows([newRow()]); setWarnings([]);
    imageRef.current = next; setImages(next);
  }
  function addFiles(files, replacing = null) {
    const chosen = [...files];
    if (!chosen.length) return;
    if (chosen.some(f => !TYPES.includes(f.type))) {setError("Choose JPEG, PNG or WebP photos."); return;}
    if (chosen.some(f => f.size > LIMIT || !f.size)) {setError("Each photo must be nonempty and 8 MB or smaller."); return;}
    const remaining = replacing ? images.filter(i => i.id !== replacing) : images;
    if (remaining.length + chosen.length > 5) {setError("Upload up to five photos per prescription."); return;}
    const old = images.find(i => i.id === replacing);
    if (old) URL.revokeObjectURL(old.url);
    const added = chosen.map(file => ({id: crypto.randomUUID(), file, url: URL.createObjectURL(file)}));
    updateImages([...remaining, ...added]);
  }
  function removeImage(id) {
    const image = images.find(i => i.id === id);
    if (image) URL.revokeObjectURL(image.url);
    updateImages(images.filter(i => i.id !== id));
  }
  async function extract() {
    invalidate(); setRows([]); setWarnings([]);
    const id = version.current, abort = new AbortController(); controller.current = abort;
    const extracted = [], notes = [];
    try {
      for (let i = 0; i < images.length; i++) {
        setBusy(`Reading photo ${i + 1} of ${images.length}…`);
        const photo = images[i];
        const data = await request("/api/prescription/extract", {method: "POST", headers: {"Content-Type": photo.file.type}, body: photo.file, signal: abort.signal});
        if (version.current !== id) return;
        extracted.push(...data.medications.map(m => ({...newRow(), ...m, photo: i + 1})));
        notes.push(...data.warnings.map(w => `Photo ${i + 1}: ${w}`));
      }
      if (extracted.length > 20) notes.push("More than 20 medications were extracted. Split the list before checking.");
      if (!extracted.length) notes.push("No readable medications were found. Try a clearer photo or enter them manually.");
      setRows(extracted.length ? extracted : [newRow()]); setWarnings(notes);
    } catch(e) {if (e.name !== "AbortError" && id === version.current) {setError(e.message); setRows([newRow()]);}}
    finally {if (id === version.current) setBusy("");}
  }
  function editRow(id, field, value) {
    invalidate(); setRows(current => current.map(r => r.id === id ? {...r, [field]: value} : r));
  }
  const names = rows.map(r => r.name.trim());
  const valid = names.length > 0 && names.length <= 20 && names.every(Boolean);
  async function matchNames() {
    invalidate(); const id = version.current, abort = new AbortController(); controller.current = abort;
    setBusy("Matching names and aliases…");
    try {const data = await post("/api/prescription/resolve", {medications: names}, abort.signal); if (id === version.current) setMatches(data.medications);}
    catch(e) {if (e.name !== "AbortError" && id === version.current) setError(e.message);}
    finally {if (id === version.current) setBusy("");}
  }
  async function analyze() {
    const id = ++version.current, abort = new AbortController(); controller.current = abort;
    setResult(null); setError(""); setBusy("Checking every ingredient pair and preparing your explanation…");
    try {const data = await post("/api/prescription/analyze", {medications: names, confirmed}, abort.signal); if (id === version.current) setResult(data);}
    catch(e) {if (e.name !== "AbortError" && id === version.current) setError(e.message);}
    finally {if (id === version.current) setBusy("");}
  }
  function reset() {images.forEach(i => URL.revokeObjectURL(i.url)); updateImages([]); if (upload.current) upload.current.value = "";}
  const sortedPairs = [...(result?.pairs ?? [])].sort((a, b) =>
    (SEVERITY_ORDER[a.verdict] ?? 3) - (SEVERITY_ORDER[b.verdict] ?? 3));
  const pairOrder = new Map(sortedPairs.map((pair, index) => [pair.pair_id, index]));
  const sortedFindings = [...(result?.explanation.findings ?? [])].sort((a, b) =>
    (pairOrder.get(a.pair_id) ?? Infinity) - (pairOrder.get(b.pair_id) ?? Infinity));
  return <div className="prescription-app">
    <header className="hero"><div><span className="eyebrow">PRESCRIPTION REVIEW</span><h1>Understand your medicines, together.</h1><p>Upload a prescription, confirm the names, and see what the interaction evidence says.</p></div><button className="secondary" onClick={reset}>Start over</button></header>
    <div className="steps" aria-label="Steps"><span>1 · Add prescription</span><span>2 · Confirm medicines</span><span>3 · Read findings</span></div>
    {error && <div className="error" role="alert">{error}</div>}
    {busy && <div className="progress" role="status">{busy}</div>}
    {config && !config.gemini_configured && <div className="notice">Photo reading needs a Gemini key on the server. You can enter medicines manually below; interaction evidence remains available.</div>}
    <section className="card"><h2>1. Add your prescription</h2><p className="muted">Use a clear photo with all medication names visible. JPEG, PNG or WebP · up to 8 MB each · up to 5 photos.</p>
      <label className="upload-zone"><span className="upload-icon">＋</span><strong>Choose prescription photos</strong><span>Preview, replace or remove them before reading</span><input ref={upload} aria-label="Upload prescription photos" type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={e => {addFiles(e.target.files); e.target.value = "";}}/></label>
      {images.length > 0 && <div className="photo-grid">{images.map((image,i) => <article className="photo" key={image.id}><a href={image.url} target="_blank" rel="noreferrer" aria-label={`Open preview of photo ${i+1}`}><img src={image.url} alt={`Prescription photo ${i+1}`}/></a><p>{image.file.name}</p><div className="row"><label className="replace">Replace<input aria-label={`Replace photo ${i+1}`} type="file" accept="image/jpeg,image/png,image/webp" onChange={e => {addFiles(e.target.files,image.id); e.target.value = "";}}/></label><button className="secondary" onClick={() => removeImage(image.id)}>Remove photo {i+1}</button></div></article>)}</div>}
      <div className="upload-actions"><button className="primary" disabled={!images.length || !!busy || config?.gemini_configured === false} onClick={extract}>Read medications with Gemini</button><span className="muted small">Photos are sent to Google Gemini for extraction and are not saved by this app. You can enter names manually instead.</span></div>
    </section>
    <section className="card"><h2>2. Review medication names</h2><p className="muted">Check each name against your prescription. Correct unclear text; keep strength and formulation separate. We also match verified aliases and regional names, including paracetamol.</p>
      {warnings.map((w,i) => <p className="notice" key={i}>{w}</p>)}
      <div className="medication-editor">{rows.map((row,i) => <div className={`medication-row ${row.uncertain ? "uncertain" : ""}`} key={row.id}><div><label htmlFor={`name-${row.id}`}>Medicine {i+1}{row.photo ? ` · photo ${row.photo}` : ""}</label><input id={`name-${row.id}`} value={row.name} maxLength={160} placeholder="e.g. paracetamol" onChange={e => editRow(row.id,"name",e.target.value)}/>{row.uncertain && <span className="review-note">Uncertain reading — verify this name</span>}{row.note && <small>{row.note}</small>}</div><div><label htmlFor={`strength-${row.id}`}>Strength (optional)</label><input id={`strength-${row.id}`} value={row.strength} onChange={e => editRow(row.id,"strength",e.target.value)} placeholder="As written"/></div><div><label htmlFor={`form-${row.id}`}>Form (optional)</label><input id={`form-${row.id}`} value={row.formulation} onChange={e => editRow(row.id,"formulation",e.target.value)} placeholder="e.g. tablet"/></div><button className="secondary" aria-label={`Remove medicine ${i+1}`} onClick={() => {invalidate(); setRows(rows.filter(r => r.id !== row.id));}}>Remove</button></div>)}</div>
      <div className="row editor-actions"><button className="secondary" disabled={rows.length >= 20} onClick={() => {invalidate(); setRows([...rows,newRow()]);}}>＋ Add medicine</button><button className="primary" disabled={!valid || !!busy} onClick={matchNames}>Match names and aliases</button></div>
      {matches && <div className="match-review"><h3>Matched active ingredients</h3>{matches.map((m,i) => <div className="match" key={i}><b>{m.input}</b><span>→</span><span>{m.ingredients.length ? m.ingredients.map(d => d.name).join(" + ") : "Not matched"}</span>{m.message && <small>{m.message} Interactions involving this entry cannot be checked.</small>}</div>)}
        <label className="confirmation"><input type="checkbox" checked={confirmed} onChange={e => {version.current++; controller.current?.abort(); setBusy(""); setConfirmed(e.target.checked); setResult(null);}}/>I have checked the medication names and matched ingredients against my prescription.</label><button className="primary" disabled={!confirmed || !!busy} onClick={analyze}>Check all medication pairs</button></div>}
    </section>
    {result && <section className="card results" aria-live="polite"><span className="eyebrow">YOUR RESULTS</span><h2>3. What the evidence says</h2><p>{result.coverage.checked_pairs} unique ingredient pairs checked across {result.coverage.unique_ingredients} ingredients.</p>
      <div className="notice">{result.notice} Strength, dose, timing, health conditions and personal risk are not assessed.</div>
      {result.explanation.data_gaps.length > 0 && <div className="coverage"><h3>Missing or incomplete data</h3><ul>{result.explanation.data_gaps.map((text,i) => <li key={i}>{text}</li>)}</ul></div>}
      {sortedFindings.map(f => {const pair=result.pairs.find(p => p.pair_id===f.pair_id); return <article className="finding" key={f.pair_id}><div className="finding-title"><h3>{pair.drugs.join(" + ")}</h3><span className={`pill p-${pair.verdict}`}>{pair.verdict === "BLOCK" ? "Review needed · major finding" : pair.verdict.replaceAll("_"," ")}</span></div><p>{f.explanation}</p><div className="citations">{f.evidence_ids.map(id => <a key={id} href={`#evidence-${id}`}>Source {id}</a>)}</div></article>;})}
      <section className="coverage"><h3>OpenFDA medicine descriptions and label context</h3><p>Representative product labels matched through RxNorm ingredients. Formulations and manufacturers may differ from your prescription. These excerpts do not assess your health conditions or establish an interaction between your medicines.</p>
        {result.label_descriptions?.length ? result.label_descriptions.map(label => <article key={label.ingredient_key}>
          <h4>{label.drug}</h4><p className="muted small">Label date: {label.effective_time || "not recorded"} · <a href={label.source_url} target="_blank" rel="noreferrer">Read the source label</a></p>
          {result.explanation.label_findings?.filter(f => f.ingredient_key === label.ingredient_key).map(f => <div className="label-explanation" key={f.ingredient_key}>
            <h5>About this medicine</h5><p>{f.explanation}</p><div className="citations">{f.evidence_ids.map(id => <a key={id} href={`#label-source-${id}`}>Label source {id}</a>)}</div>
            {!!f.warnings.length && <details><summary>Label warnings to review</summary>{f.warnings.map((warning, i) => <article key={i}><p>{warning.text}</p><div className="citations">{warning.evidence_ids.map(id => <a key={id} href={`#label-source-${id}`}>Label source {id}</a>)}</div></article>)}</details>}
          </div>)}
          {label.sections.map(section => <details id={`label-source-${section.evidence_id}`} key={section.section} open={section.section === "description"}><summary>{section.section.replaceAll("_", " ")}</summary><p style={{whiteSpace: "pre-wrap"}}>{section.text}</p>{section.truncated && <p className="muted small">Excerpt shortened. Read the source label for the full section.</p>}</details>)}
        </article>) : <p>No matching OpenFDA product label was found in the loaded data. This does not mean the medicines have no warnings.</p>}
        {!!result.label_context?.length && <details><summary>Previously loaded condition excerpts</summary>{result.label_context.map((label, i) => <article key={i}><h4>{label.drug} · {label.condition}</h4><p>{label.text_snippet || "No label excerpt was stored."}</p><p className="muted small">Source: {label.source || "openFDA"} · {label.section || "section not recorded"}. Stored excerpt; may be incomplete.</p></article>)}</details>}
      </section>
      <p className="muted small">Explanation: {result.explanation.source === "gemini" ? "Google Gemini, grounded in returned graph evidence" : "Graph evidence template"}. {result.explanation.reason}</p>
      <details><summary>All checked pairs ({result.pairs.length})</summary>{sortedPairs.map(p => <p key={p.pair_id}>{p.drugs.join(" + ")} — {p.paths.length ? `${p.paths.length} evidence paths` : "No interaction data found in loaded datasets"}</p>)}</details>
      {result.evidence.length > 0 && <details className="sources" open><summary>Source records and graph paths</summary>{result.evidence.map(e => <article id={`evidence-${e.evidence_id}`} className="source-record" key={e.evidence_id}><h3>{e.evidence_id} · {e.kind === "ddinter" ? "DDInter" : "FDA enzyme / transporter mechanism"}</h3><p>{e.current_drug} + {e.proposed_drug}</p><p>{e.kind === "ddinter" ? `Reported severity: ${e.strength || "unknown"}` : `${e.actor_drug} → ${e.relationship} → ${e.target} ← SUBSTRATE_OF ← ${e.substrate_drug}`}</p><p className="muted small">Source reference: {e.source_reference || "not provided"}. Record: {e.source_evidence_id || "not provided"}.</p><a href={e.source_dataset_url} target="_blank" rel="noreferrer">Open source dataset</a><p className="muted small">{e.source_link_scope}</p><details><summary>Raw source fields</summary><pre>{JSON.stringify(e,null,2)}</pre></details></article>)}</details>}
    </section>}
    <footer>Evidence from loaded DDInter and FDA CYP data. For understanding your prescription; not a substitute for pharmacist or prescriber review.</footer>
  </div>;
}
