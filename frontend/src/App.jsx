import { useEffect, useRef, useState } from "react";

import QueryGraph from "./QueryGraph";
import { Atom, ArrowRight, ArrowLeft, Camera, ImagePlus, Upload, ScanLine, ListChecks, Network, ShieldCheck, Info, LockKeyhole, Plus, Trash2, Pencil, RotateCcw, X, Check } from "lucide-react";

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
  const [page, setPage] = useState(() => ["welcome", "upload", "review", "results"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "welcome");
  const [tab, setTab] = useState("interactions");
  function navigate(next) { location.hash = next; setPage(next); window.scrollTo(0, 0); }
  useEffect(() => { const sync = () => setPage(["welcome", "upload", "review", "results"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "welcome"); window.addEventListener("hashchange", sync); return () => window.removeEventListener("hashchange", sync); }, []);
  const [images, setImages] = useState([]), [rows, setRows] = useState([newRow()]);
  const [warnings, setWarnings] = useState([]), [matches, setMatches] = useState(null);
  const [confirmed, setConfirmed] = useState(false), [result, setResult] = useState(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState("");
  const [config, setConfig] = useState(null);
  const version = useRef(0), controller = useRef(null), imageRef = useRef([]);
  const upload = useRef(null), camera = useRef(null);
  const [info, setInfo] = useState(null), [dragging, setDragging] = useState(false);
  const dialog = useRef(null);
  useEffect(() => { if(info) dialog.current?.showModal(); }, [info]);
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
      setRows(extracted.length ? extracted : [newRow()]); setWarnings(notes); navigate("review");
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
    try {const data = await post("/api/prescription/analyze", {medications: names, confirmed}, abort.signal); if (id === version.current) { setResult(data); setTab("interactions"); navigate("results"); }}
    catch(e) {if (e.name !== "AbortError" && id === version.current) setError(e.message);}
    finally {if (id === version.current) setBusy("");}
  }
  function reset() {images.forEach(i => URL.revokeObjectURL(i.url)); updateImages([]); if (upload.current) upload.current.value = ""; navigate("upload");}
  const sortedPairs = [...(result?.pairs ?? [])].sort((a, b) =>
    (SEVERITY_ORDER[a.verdict] ?? 3) - (SEVERITY_ORDER[b.verdict] ?? 3));
  const pairOrder = new Map(sortedPairs.map((pair, index) => [pair.pair_id, index]));
  const sortedFindings = [...(result?.explanation.findings ?? [])].sort((a, b) =>
    (pairOrder.get(a.pair_id) ?? Infinity) - (pairOrder.get(b.pair_id) ?? Infinity));
  const currentStep = ["welcome", "upload", "review", "results"].indexOf(page);
  return <div className="site-shell">
    <header className="site-header"><div className="header-inner"><a className="brand" href="#welcome" aria-label="Drug Safety home"><span className="brand-mark"><Atom size={25}/></span><span>Drug Safety</span><small>Prescription review</small></a><div className="header-links"><button onClick={() => setInfo("how")}>How it works</button><button onClick={() => setInfo("evidence")}>About the evidence</button><span className="live-badge">PRESCRIPTION REVIEW</span></div></div></header>
    {page !== "welcome" && <nav className="steps" aria-label="Prescription pages">{[["welcome", "Welcome"], ["upload", "Add prescription"], ["review", "Review medicines"], ["results", "Explore results"]].map(([key,label],i) => <a key={key} href={`#${key}`} className={i < currentStep ? "completed" : ""} aria-current={page === key ? "page" : undefined}><span>{i+1}</span>{label}</a>)}</nav>}
    <main className={`prescription-app page-${page}`}>
    {error && <div className="error" role="alert">{error}</div>}
    {busy && <div className="progress" role="status"><span className="loading-dot"/>{busy}</div>}
    {page === "welcome" && <>
      <section className="welcome-hero"><div className="hero-copy"><span className="hero-eyebrow">MEDICINES. EVIDENCE. UNDERSTANDING.</span><h1>Understand your<br/>medicines, together.</h1><p>Upload a prescription, confirm the names, and see what the interaction evidence says.</p><div className="hero-action"><button className="primary" onClick={() => navigate("upload")}>Next <ArrowRight size={19}/></button><span>Start with your prescription</span></div><div className="hero-footnote"><ShieldCheck size={17}/> Built to support a pharmacist conversation, not replace it.</div></div><span className="hero-caption">CONNECTED BY CHEMISTRY</span></section>
      <section className="welcome-process"><div className="section-title"><h2>From prescription to perspective.</h2><p>Three clear steps. You stay in control.</p></div><div className="process-grid">{[["01", ScanLine, "Add your prescription", "Upload a clear photo or use your camera. Keep personal details out of the frame."], ["02", ListChecks, "Make sure it matches", "Review medicine names, strengths and forms, then confirm the active ingredients."], ["03", Network, "Explore the evidence", "See possible interactions and take informed questions to your care team."]].map(([n,Icon,title,body]) => <article key={n}><div className="process-number"><span>{n}</span><Icon size={21}/></div><h3>{title}</h3><p>{body}</p></article>)}</div><p className="welcome-disclaimer"><Info size={16}/>Missing or unavailable evidence does not establish safety. Do not change medicines based on this tool alone.</p></section>
    </>}
    {page === "upload" && <>
      <PageHeading step="02 / ADD PRESCRIPTION" title="Let’s start with your prescription." description="Choose the way that works for you. You’ll review every medicine before any interaction check."/>
      {config && !config.gemini_configured && <div className="notice">Photo reading needs a Gemini key on the server. You can enter medicines manually; interaction evidence remains available.</div>}
      <div className="capture-grid"><section className={`capture-card ${dragging ? "dragging" : ""}`} onDragOver={e => {e.preventDefault(); setDragging(true);}} onDragLeave={() => setDragging(false)} onDrop={e => {e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files);}}><span className="feature-icon"><ImagePlus size={25}/></span><h2>Upload prescription photos</h2><p>Drag and drop your photos here, or choose from your device.</p><button className="secondary" onClick={() => upload.current.click()}>Choose photos <Upload size={16}/></button><input className="file-input" ref={upload} aria-label="Upload prescription photos" type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={e => {addFiles(e.target.files); e.target.value = "";}}/><small>JPEG, PNG or WebP · Up to 8 MB each · Maximum 5 photos</small></section>
      <section className="capture-card"><span className="feature-icon"><Camera size={25}/></span><h2>Take a new photo</h2><p>Use your device camera to capture a clear, well-lit photo.</p><button className="secondary" onClick={() => camera.current.click()}>Open camera <Camera size={16}/></button><input className="file-input" ref={camera} aria-label="Take prescription photo" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={e => {addFiles(e.target.files); e.target.value = "";}}/><small>Camera access requires your device permission.</small></section></div>
      <div className="capture-tips"><article><h3><ScanLine size={17}/>A clearer photo means a better starting point.</h3><p>Keep medicine names and strengths in focus. Include the full prescription, avoid glare, and cover names, addresses or other identifying details.</p></article><article><h3><LockKeyhole size={17}/>Before you share a photo</h3><p>Photos are sent to Google Gemini to extract medicine details. This app does not save your prescription photos.</p><button className="text-button" onClick={() => setInfo("privacy")}>Read privacy &amp; data information <ArrowRight size={13}/></button></article></div>
      <div className="photo-status"><span><ImagePlus size={16}/>{images.length ? `${images.length} prescription ${images.length === 1 ? "photo" : "photos"} ready` : "No photos added yet"}</span><small>{images.length} of 5 photos</small></div>
      {images.length > 0 && <div className="photo-grid">{images.map((image,i) => <article className="photo" key={image.id}><a href={image.url} target="_blank" rel="noreferrer" aria-label={`Open preview of photo ${i+1}`}><img src={image.url} alt={`Prescription photo ${i+1}`}/></a><p>{image.file.name}</p><div className="row"><label className="replace">Replace<input aria-label={`Replace photo ${i+1}`} type="file" accept="image/jpeg,image/png,image/webp" onChange={e => {addFiles(e.target.files,image.id); e.target.value = "";}}/></label><button className="secondary" onClick={() => removeImage(image.id)}>Remove photo {i+1}</button></div></article>)}</div>}
      <div className="page-actions"><button className="text-button muted" onClick={() => navigate("welcome")}><ArrowLeft size={15}/>Back to welcome</button><div className="action-group"><button className="secondary" onClick={() => navigate("review")}>Enter medicines manually</button><button className="primary" disabled={!images.length || !!busy || config?.gemini_configured === false} onClick={extract}>Read medications with Gemini <ArrowRight size={17}/></button></div></div>
    </>}
    {page === "review" && <>
      <PageHeading step="03 / REVIEW MEDICINES" title="The details make a difference." description="Check each name, strength and form. Confirm the active ingredients before exploring possible interactions."/>
      <div className="analysis-strip"><ListChecks size={16}/><span>Review every medicine against your prescription. Correct any uncertain readings before you continue.</span></div>
      {warnings.map((w,i) => <p className="notice" key={i}>{w}</p>)}
      <section className="medicine-card"><div className="medicine-card-header"><h2>Your medicine list <span className="count-badge">{rows.length} {rows.length === 1 ? "medicine" : "medicines"}</span></h2><button className="text-button" disabled={rows.length >= 20} onClick={() => {invalidate(); setRows([...rows,newRow()]);}}><Plus size={15}/>Add medicine</button></div>
      <div className="table-head"><span>MEDICINE NAME</span><span>STRENGTH</span><span>TYPE / FORM</span><span>ACTIVE INGREDIENT</span><span/></div>
      <div className="medication-editor">{rows.map((row,i) => <div className={`medication-row ${row.uncertain ? "uncertain" : ""}`} key={row.id}><div><label className="sr-only" htmlFor={`name-${row.id}`}>Medicine {i+1}{row.photo ? ` · photo ${row.photo}` : ""}</label><input id={`name-${row.id}`} value={row.name} maxLength={160} placeholder="Medicine name" onChange={e => editRow(row.id,"name",e.target.value)}/>{row.uncertain && <span className="review-note">Uncertain reading — verify this name</span>}{row.note && <small>{row.note}</small>}</div><div><label className="sr-only" htmlFor={`strength-${row.id}`}>Strength (optional)</label><input id={`strength-${row.id}`} value={row.strength} onChange={e => editRow(row.id,"strength",e.target.value)} placeholder="e.g. 20 mg"/></div><div><label className="sr-only" htmlFor={`form-${row.id}`}>Form (optional)</label><input id={`form-${row.id}`} value={row.formulation} onChange={e => editRow(row.id,"formulation",e.target.value)} placeholder="e.g. tablet"/></div><div className="ingredient-match match">{matches ? <><span className={`match-symbol ${matches[i]?.ingredients.length ? "" : "unresolved"}`}>{matches[i]?.ingredients.length ? <Check size={15}/> : <Info size={15}/>}</span><div><strong>{matches[i]?.ingredients.length ? matches[i].ingredients.map(d => d.name).join(" + ") : "Not matched"}</strong><small>{matches[i]?.ingredients.length ? "Matched active ingredient" : "Check name or enter ingredients"}</small><span className="sr-only">{matches[i]?.input}</span></div></> : <small>Match to verify</small>}</div><button className="icon-button" aria-label={`Remove medicine ${i+1}`} onClick={() => {invalidate(); setRows(rows.filter(r => r.id !== row.id));}}><Trash2 size={17}/></button></div>)}</div>
      <div className="medicine-note"><Info size={15}/>Interaction checks compare active ingredient pairs. Strength, dose and timing are not assessed.</div></section>
      <div className="match-actions"><button className="secondary" disabled={!valid || !!busy} onClick={matchNames}>Match names and aliases <ListChecks size={16}/></button><span className="muted small">Verified aliases and regional names are supported, including paracetamol.</span></div>
      {matches && <div className="match-review">{matches.filter(m => m.message).map((m,i) => <p className="notice" key={i}><strong>{m.input}: </strong>{m.message} Interactions involving this entry cannot be checked.</p>)}<label className="confirmation"><input type="checkbox" checked={confirmed} onChange={e => {version.current++; controller.current?.abort(); setBusy(""); setConfirmed(e.target.checked); setResult(null);}}/><span>I have checked the medication names and matched ingredients against my prescription.<small>This confirms the names and ingredient matches, not clinical suitability.</small></span></label></div>}
      <CareNote/>
      <div className="page-actions"><button className="secondary" onClick={() => navigate("upload")}><ArrowLeft size={16}/>Back to prescription</button><div className="action-group"><span className="muted small">{rows.length} medicines</span><button className="primary" disabled={!matches || !confirmed || !!busy} onClick={analyze}>Check all medication pairs <ArrowRight size={17}/></button></div></div>
    </>}
    {page === "results" && !result && <section className="card"><h2>No analysis yet</h2><p>Confirm your medication names to run a new check.</p><button className="primary" onClick={() => navigate("review")}>Review medicines</button></section>}
    {page === "results" && result && <section className="results" aria-live="polite"><div className="result-heading"><div><span className="eyebrow">STEP 04 / EXPLORE RESULTS</span><h1>Possible interactions, in context.</h1><p className="muted">A guide to the questions worth asking your pharmacist or prescriber.</p></div><button className="secondary" onClick={() => navigate("review")}>Edit medicines <Pencil size={15}/></button></div>
      <div className="analysis-strip"><Network size={16}/><span>{result.coverage.unique_ingredients} ingredients · {result.coverage.checked_pairs} pairs checked · Evidence from loaded DDInter and FDA data{!result.coverage.complete ? " · Coverage limitations below" : ""}</span></div>

      <div className="results-layout"><aside className="graph-panel"><QueryGraph result={result}/></aside><div className="result-content">
      <div className="result-tabs" role="tablist" aria-label="Result views">{[["interactions", "Interactions & severity"], ["labels", "OpenFDA data"], ["technical", "For hackathon judges"]].map(([key, label]) => <button key={key} id={`tab-${key}`} role="tab" aria-selected={tab === key} aria-controls={`panel-${key}`} tabIndex={tab === key ? 0 : -1} onClick={() => setTab(key)} onKeyDown={e => { const keys = ["interactions", "labels", "technical"]; const index = keys.indexOf(key); const next = e.key === "ArrowRight" ? keys[(index + 1) % 3] : e.key === "ArrowLeft" ? keys[(index + 2) % 3] : e.key === "Home" ? keys[0] : e.key === "End" ? keys[2] : null; if(next) {e.preventDefault(); setTab(next); document.getElementById(`tab-${next}`).focus();} }}>{label}</button>)}</div>
      <div role="tabpanel" id="panel-interactions" aria-labelledby="tab-interactions" hidden={tab !== "interactions"}><h2 className="panel-heading">Drug–drug connections <span>{sortedPairs.length} pairs checked</span></h2>
      {sortedPairs.length === 0 && <p>At least two matched ingredients are needed for a drug-drug check.</p>}

      {sortedFindings.map(f => {const pair=result.pairs.find(p => p.pair_id===f.pair_id); return <article className="finding" key={f.pair_id}><div className="finding-title"><h3>{pair.drugs.join(" + ")}</h3><span className={`pill p-${pair.verdict}`}>{pair.verdict === "BLOCK" ? "Review needed · major finding" : pair.verdict.replaceAll("_"," ")}</span></div><h4 className="finding-summary">{pair.summary}</h4><p>{f.explanation}</p><div className="citations">{f.evidence_ids.map(id => <a key={id} href="#results" onClick={e => {e.preventDefault(); setTab("technical"); requestAnimationFrame(() => document.getElementById(`evidence-${id}`)?.scrollIntoView({block:"center"}));}}>Source {id}</a>)}</div></article>;})}
      {result.explanation.data_gaps.length > 0 && <div className="coverage"><h3>Missing or incomplete data</h3><ul>{result.explanation.data_gaps.map((text,i) => <li key={i}>{text}</li>)}</ul></div>}
      </div><div role="tabpanel" id="panel-labels" aria-labelledby="tab-labels" hidden={tab !== "labels"}><section className="coverage"><h3>OpenFDA medicine descriptions and label context</h3><p>Representative product labels matched through RxNorm ingredients. Formulations and manufacturers may differ from your prescription. These excerpts do not assess your health conditions or establish an interaction between your medicines.</p>
        {result.medications.filter(m => !result.label_descriptions?.some(l => l.ingredient_key === m.ingredients.map(d => String(d.rxcui)).sort().join("|"))).map((m,i) => <p key={i}>{m.input}: No matching OpenFDA label available.</p>)}
        {result.label_descriptions?.length ? result.label_descriptions.map(label => <details className="drug-label" key={label.ingredient_key}><summary>{label.drug} - {label.sections.length} available label sections</summary><p className="muted small">Label date: {label.effective_time || "not recorded"} · <a href={label.source_url} target="_blank" rel="noreferrer">Read the source label</a></p>
          {result.explanation.label_findings?.filter(f => f.ingredient_key === label.ingredient_key).map(f => <div className="label-explanation" key={f.ingredient_key}>
            <h5>About this medicine</h5><p>{f.explanation}</p><div className="citations">{f.evidence_ids.map(id => <a key={id} href={`#label-source-${id}`} onClick={e => {e.preventDefault(); const target = document.getElementById(`label-source-${id}`); if(target) {target.open = true; target.scrollIntoView({block:"center"});}}}>Label source {id}</a>)}</div>
            {!!f.warnings.length && <details><summary>Label warnings to review</summary>{f.warnings.map((warning, i) => <article key={i}><p>{warning.text}</p><div className="citations">{warning.evidence_ids.map(id => <a key={id} href={`#label-source-${id}`} onClick={e => {e.preventDefault(); const target = document.getElementById(`label-source-${id}`); if(target) {target.open = true; target.scrollIntoView({block:"center"});}}}>Label source {id}</a>)}</div></article>)}</details>}
          </div>)}
          {label.sections.map(section => <details id={`label-source-${section.evidence_id}`} key={section.section} open={section.section === "description"}><summary>{section.section.replaceAll("_", " ")}</summary><p style={{whiteSpace: "pre-wrap"}}>{section.text}</p>{section.truncated && <p className="muted small">Excerpt shortened. Read the source label for the full section.</p>}</details>)}
        </details>) : <p>No matching OpenFDA product label was found in the loaded data. This does not mean the medicines have no warnings.</p>}
        {!!result.label_context?.length && <details><summary>Previously loaded condition excerpts</summary>{result.label_context.map((label, i) => <article key={i}><h4>{label.drug} · {label.condition}</h4><p>{label.text_snippet || "No label excerpt was stored."}</p><p className="muted small">Source: {label.source || "openFDA"} · {label.section || "section not recorded"}. Stored excerpt; may be incomplete.</p></article>)}</details>}
      </section>
      </div><div role="tabpanel" id="panel-technical" aria-labelledby="tab-technical" hidden={tab !== "technical"}>
      <h3>Executed graph queries</h3>{result.query_trace?.length ? result.query_trace.map((q,i) => <details key={i}><summary>Query {i+1} - {q.row_count} rows</summary><pre>{q.cypher}</pre><pre>{JSON.stringify(q.parameters,null,2)}</pre></details>) : <p>Query tracing is unavailable for this analysis.</p>}
      <details><summary>All returned analysis data</summary><pre>{JSON.stringify(result,null,2)}</pre></details>
      <p className="muted small">Explanation: {result.explanation.source === "gemini" ? "Google Gemini, grounded in returned graph evidence" : "Graph evidence template"}. {result.explanation.reason}</p>
      <details><summary>All checked pairs ({result.pairs.length})</summary>{sortedPairs.map(p => <p key={p.pair_id}>{p.drugs.join(" + ")} — {p.paths.length ? `${p.paths.length} evidence paths` : "No interaction data found in loaded datasets"}</p>)}</details>
      {result.evidence.length > 0 && <details className="sources" open><summary>Source records and graph paths</summary>{result.evidence.map(e => <article id={`evidence-${e.evidence_id}`} className="source-record" key={e.evidence_id}><h3>{e.evidence_id} · {e.kind === "ddinter" ? "DDInter" : "FDA enzyme / transporter mechanism"}</h3><p>{e.current_drug} + {e.proposed_drug}</p><p>{e.kind === "ddinter" ? `Reported severity: ${e.strength || "unknown"}` : `${e.actor_drug} → ${e.relationship} → ${e.target} ← SUBSTRATE_OF ← ${e.substrate_drug}`}</p><p className="muted small">Source reference: {e.source_reference || "not provided"}. Record: {e.source_evidence_id || "not provided"}.</p><a href={e.source_dataset_url} target="_blank" rel="noreferrer">Open source dataset</a><p className="muted small">{e.source_link_scope}</p><details><summary>Raw source fields</summary><pre>{JSON.stringify(e,null,2)}</pre></details></article>)}</details>}
      </div></div></div><CareNote/><div className="page-actions"><div><strong>Bring the full picture to your care team.</strong><p className="muted small">Include supplements, non-prescription medicines and your health history.</p></div><button className="secondary" onClick={reset}>Start a new review <RotateCcw size={16}/></button></div>
    </section>}
    </main>
    <footer className="site-footer"><span>Drug Safety · Informed questions. Better conversations.</span><div><button onClick={() => setInfo("privacy")}>Privacy &amp; data</button><button onClick={() => setInfo("limitations")}>Limitations</button><span>Evidence-led review</span></div></footer>
    {info && <dialog ref={dialog} className="info-dialog" onClose={() => setInfo(null)} onClick={e => {if(e.target === e.currentTarget) dialog.current.close();}}><button className="icon-button dialog-close" aria-label="Close information" onClick={() => dialog.current.close()}><X size={20}/></button><h2>{info === "how" ? "From prescription to perspective" : info === "evidence" ? "About the evidence" : info === "privacy" ? "Privacy & data" : "What this review can tell you"}</h2><p>{info === "how" ? "Add photos or enter medicines manually. Review every extracted name, strength and form, match the active ingredients, then confirm the names to check every unique ingredient pair. Explore interactions, available OpenFDA labels, and the source records behind your results." : info === "evidence" ? "Interaction checks use the loaded DDInter dataset and FDA enzyme and transporter mechanisms. OpenFDA product labels provide separate drug-level context. The results include source records and executed graph queries. Missing evidence does not establish safety." : info === "privacy" ? "Prescription photos are sent to Google Gemini for medication extraction and are not saved by this app. Cover personal information before uploading. Medication names are sent to this app’s server for ingredient matching and graph analysis. Uploaded photos and review state are held in this browser session and are cleared when the page is reloaded or you start a new review." : "This tool compares active ingredient pairs from loaded evidence. It does not assess dose, timing, health conditions or your personal risk. A representative OpenFDA label may differ from your formulation or manufacturer. Discuss results with a pharmacist or prescriber; do not change medicines based on this tool alone."}</p><button className="primary" onClick={() => dialog.current.close()}>Got it</button></dialog>}
  </div>;
}

function PageHeading({step, title, description}) {
  return <div className="page-heading"><span className="eyebrow">STEP {step}</span><h1>{title}</h1><p>{description}</p></div>;
}
function CareNote() {
  return <div className="care-note"><ShieldCheck size={20}/><div><strong>A starting point for a conversation, not a clinical decision.</strong><p>Dose, timing, health conditions and personal risk are not assessed. This is not a substitute for pharmacist or prescriber review. Do not change medicines based on this tool alone.</p></div></div>;
}
