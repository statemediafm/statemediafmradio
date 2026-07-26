"""FastAPI app for the M1 slice: /health, /plan, /audio/{id}, and a minimal page.

The web layer is deliberately thin (plan §5.6): it holds the latest plan + an
in-memory audio store and renders/serves them. It contains no summarization or
scheduling logic. FastAPI is imported lazily so the rest of the package (and the
CLI/tests) work without the web dependency installed.
"""

from __future__ import annotations

from ..core.models import AudioRef, BroadcastPlan, StrudelProgram
from ..core.plan import plan_to_dict


def program_to_dict(program: StrudelProgram) -> dict:
    """JSON view of a StrudelProgram — the client plays ``text`` and crossfades
    over ``fade_ms`` between polls."""
    return {
        "text": program.text,
        "style": program.style,
        "intensity": program.intensity,
        "brainwave_band": program.brainwave_band,
        "fade_ms": program.fade_ms,
    }


class _State:
    """In-memory store for the current plan, audio clips, and music (M1–M2)."""

    def __init__(self) -> None:
        self.plan: BroadcastPlan | None = None
        self.audio: dict[str, AudioRef] = {}
        self.program: StrudelProgram | None = None
        self.model: str = "Entrainment 0.1"  # the selected ambient generator (default)
        self.show_selector: bool = False  # show the generator dropdown in the UI? (config)
        self.tuning: float = 440.0  # concert-A reference (Hz) for all notes
        self.broadcasting: bool = True  # when False the refresh loop pauses (no polling/TTS/LLM)
        self.quiet_mode: bool = False  # music only around the news, silent between
        self.music_on: bool = True  # the quiet-mode gate (should the music sound now?)
        self.last_signal = None  # last ActivitySignal, for immediate model/tuning switches
        self.news_model: str | None = None  # LLM model for news parsing (None → offline copy)
        self.news_models: list[str] = []  # gateway models the Settings tab offers
        self.news_cfg = None  # base LLMConfig (for gateway model auto-discovery)

    def set_plan(self, plan: BroadcastPlan) -> None:
        self.plan = plan
        self.audio = {s.audio.id: s.audio for s in plan.segments if s.audio}

    def set_program(self, program: StrudelProgram) -> None:
        self.program = program


def create_app(state: _State | None = None):
    """Build the FastAPI application. Call ``app.state.store.set_plan(...)`` to
    publish a plan for the page and API to serve."""
    from fastapi import Body, FastAPI, HTTPException, Response
    from fastapi.responses import HTMLResponse

    store = state or _State()
    app = FastAPI(title="Maelcom", version="0.1.0")
    app.state.store = store

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/plan")
    def plan() -> dict:
        if store.plan is None:
            return {"segments": []}
        return plan_to_dict(store.plan)

    @app.get("/genmusic")
    def genmusic() -> dict:
        if store.program is None:
            return {"text": None, "play": store.music_on}
        # `play` is the quiet-mode gate: the client silences the music when False.
        return {**program_to_dict(store.program), "play": store.music_on}

    @app.get("/broadcast")
    def broadcast() -> dict:
        return {"broadcasting": store.broadcasting}

    @app.post("/broadcast")
    def set_broadcast(on: bool) -> dict:
        """Stop/resume the broadcast. Stopping pauses the server refresh loop
        (no more polling/TTS/LLM) and silences the music; resuming restores both."""
        store.broadcasting = on
        store.music_on = on  # silence the audio when stopped; restore on resume
        return {"broadcasting": store.broadcasting}

    @app.get("/quiet")
    def quiet() -> dict:
        return {"quiet_mode": store.quiet_mode, "music_on": store.music_on}

    @app.post("/quiet")
    def set_quiet(on: bool) -> dict:
        """Turn quiet mode on/off. Off resumes continuous play immediately."""
        store.quiet_mode = on
        if not on:
            store.music_on = True
        return {"quiet_mode": store.quiet_mode, "music_on": store.music_on}

    @app.get("/models")
    def models() -> dict:
        """The user-selectable ambient generators, the current one, and whether
        the UI should show the selector at all (off by default, set by config)."""
        from ..genmusic.styles import AMBIENT_MODELS

        return {
            "models": list(AMBIENT_MODELS),
            "current": store.model,
            "selector": store.show_selector,
        }

    @app.post("/model")
    def set_model(name: str) -> dict:
        """Switch the ambient generator; recompose immediately if we have a signal."""
        from ..genmusic import compose
        from ..genmusic.styles import STYLES

        if name not in STYLES:
            raise HTTPException(status_code=400, detail="unknown model")
        store.model = name
        if store.last_signal is not None:
            store.set_program(compose(store.last_signal, style=name, tuning_a=store.tuning))
        return {"current": store.model}

    @app.get("/tuning")
    def tuning() -> dict:
        """The selectable concert-A tuning references (Hz) and the current one."""
        from ..genmusic.compose import TUNINGS

        return {"tunings": list(TUNINGS), "current": store.tuning}

    @app.post("/tuning")
    def set_tuning(a: float) -> dict:
        """Set the concert-A reference (Hz); recompose immediately if we have a signal."""
        from ..genmusic import compose
        from ..genmusic.compose import TUNINGS

        if a not in TUNINGS:
            raise HTTPException(status_code=400, detail="unsupported tuning")
        store.tuning = a
        if store.last_signal is not None:
            store.set_program(compose(store.last_signal, style=store.model, tuning_a=a))
        return {"current": store.tuning}

    @app.get("/news-model")
    def news_model() -> dict:
        """The gateway model used for news parsing: the current pick, the offered
        options, and whether news parsing is live at all (``live`` is False when
        the server was started without ``--live`` — the news is the deterministic
        offline copy and the selector is hidden)."""
        return {
            "current": store.news_model,
            "models": list(store.news_models),
            "live": store.news_model is not None,
        }

    @app.post("/news-model")
    def set_news_model(name: str) -> dict:
        """Switch the news-parsing model (any model the gateway serves). Applies to
        the next news cycle; only meaningful when the server is running live."""
        if store.news_model is None:
            raise HTTPException(status_code=409, detail="news parsing is not live")
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="empty model")
        store.news_model = name
        if name not in store.news_models:
            store.news_models.append(name)  # remember a custom entry
        return {"current": store.news_model, "models": list(store.news_models)}

    @app.post("/news-model/discover")
    def discover_news_models() -> dict:
        """Auto-discover models the gateway serves (OpenAI-compatible
        ``GET {base}/models``) and merge them into the selectable options.
        Best-effort: an unreachable gateway just adds nothing."""
        if store.news_model is None:
            raise HTTPException(status_code=409, detail="news parsing is not live")
        from ..newsroom.llm import LLMConfig, discover_models

        cfg = store.news_cfg or LLMConfig(model=store.news_model)
        found = discover_models(cfg)
        merged = list(store.news_models)
        merged.extend(m for m in found if m not in merged)
        store.news_models = merged
        return {"models": merged, "discovered": found}

    @app.get("/auth")
    def auth() -> dict:
        """Per-source endpoints + whether a token is set (tokens are masked)."""
        from ..auth import AUTH_SOURCES, masked_auth

        return {"sources": list(AUTH_SOURCES), "config": masked_auth()}

    @app.post("/auth")
    def set_auth(payload: dict = Body(...)) -> dict:  # noqa: B008 (FastAPI body param)
        """Save a source's endpoint/token to the gitignored local auth file. The
        token is taken from the request body (never the URL) and only overwritten
        when a non-empty value is supplied."""
        from ..auth import AUTH_SOURCES, masked_auth, save_auth_entry

        source = payload.get("source")
        if source not in AUTH_SOURCES:
            raise HTTPException(status_code=400, detail="unknown source")
        save_auth_entry(
            source,
            endpoint=(payload.get("endpoint") or None),
            token=(payload.get("token") or None),
        )
        return {"config": masked_auth()}

    @app.get("/audio/{clip_id}")
    def audio(clip_id: str) -> Response:
        clip = store.audio.get(clip_id)
        if clip is None:
            raise HTTPException(status_code=404, detail="unknown clip")
        return Response(content=clip.data, media_type=clip.media_type)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _render_page(store)

    return app


def _render_page(store: _State) -> str:
    """The Tufte player page. Static: the browser polls /genmusic and /plan and
    plays the generative music with Strudel, crossfading as programs change."""
    return _PLAYER_HTML


# Loaded once. The page fetches /plan (news) and /genmusic (Strudel program text)
# on an interval; a start button satisfies the browser's audio-gesture rule, then
# each changed program is evaluate()'d (its built-in .fadeIn crossfades the swap).
# An incidental canvas visualizer reflects intensity + brainwave band.
_PLAYER_HTML = r"""<!doctype html><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Maelcom</title>
<style>
  body{max-width:44rem;margin:6vh auto;padding:0 1.25rem;
       font:16px/1.55 Georgia,'Times New Roman',serif;color:#111;background:#fffff8}
  h1{font-weight:normal;letter-spacing:.02em;margin:0}
  h2{font-weight:normal;font-size:1.05rem;margin:.2rem 0}
  .muted{color:#666;font-size:.85rem;font-style:italic}
  button{font:inherit;padding:.5rem 1rem;margin:1rem 0;cursor:pointer;
         background:#111;color:#fffff8;border:0;border-radius:2px}
  button[disabled]{opacity:.6;cursor:default}
  #modelwrap,#tuningwrap,#quietwrap{display:inline-block;margin-left:1rem}
  select{font:inherit;font-size:.85rem;margin-left:.35rem}
  #tabs{margin:.3rem 0 1rem;border-bottom:1px solid #ccc}
  #tabs a{cursor:pointer;display:inline-block;padding:.3rem .7rem;margin-right:.2rem;
          color:#666;border-bottom:2px solid transparent}
  #tabs a.active{color:#111;border-bottom-color:#111}
  .authrow{margin:.4rem 0;padding:.6rem 0;border-top:1px solid #eee}
  .authrow input{font:inherit;font-size:.9rem;display:block;width:100%;max-width:26rem;margin:.2rem 0;
                 padding:.3rem;border:1px solid #ccc;border-radius:2px;background:#fffff8;color:inherit}
  .authrow button{margin-top:.2rem}
  @media(prefers-color-scheme:dark){
    #tabs{border-color:#333} #tabs a.active{color:#eee;border-bottom-color:#eee}
    .authrow{border-color:#333} .authrow input{background:#111;color:#eee;border-color:#444}}
  #viz{display:block;width:100%;height:64px;margin:.5rem 0}
  article{border-top:1px solid #ccc;padding-top:.6rem;margin-top:1rem}
  audio{width:100%;margin:.4rem 0}
  @media(prefers-color-scheme:dark){
    body{background:#111;color:#eee}.muted{color:#aaa}
    button{background:#eee;color:#111}article{border-color:#333}}
</style>
<h1>Maelcom</h1>
<nav id='tabs'><a data-tab='player' class='active'>Player</a><a data-tab='settings'>Settings</a></nav>
<div id='player-view'>
<p class='muted' id='status'>internal radio · press play to begin</p>
<button id='play'>▶ Start radio</button>
<button id='stopbtn'>■ Stop broadcast</button>
<label class='muted' id='modelwrap'>ambient generator
  <select id='model'></select>
</label>
<label class='muted' id='tuningwrap'>tuning A=
  <select id='tuning'></select>
</label>
<label class='muted' id='quietwrap'><input type='checkbox' id='quiet'> quiet mode</label>
<canvas id='viz'></canvas>
<section id='news'><p class='muted'>Loading…</p></section>
</div>
<div id='settings-view' hidden>
  <div id='newsmodel-wrap' hidden>
    <h2>News-parsing model</h2>
    <p class='muted'>Which model on the <code>llm-gateway</code> writes the news.
    Pick one the gateway serves, or type a model string. Applies to the next news
    cycle.</p>
    <div class='authrow'>
      <select id='newsmodel'></select>
      <input id='newsmodel-custom' placeholder='or type a model, e.g. openai/gpt-4o-mini'>
      <button id='newsmodel-save'>Set model</button>
      <button id='newsmodel-discover'>↻ Discover from gateway</button>
      <span class='muted' id='newsmodel-status'></span>
    </div>
  </div>
  <h2>Sources &amp; auth</h2>
  <p class='muted'>Personal endpoints and tokens for the sources Maelcom polls,
  plus <code>llm-gateway</code> (the LLM/model gateway used for news parsing —
  endpoint = its base URL, token = its API key; works with LiteLLM, OpenRouter,
  Azure OpenAI, a self-hosted vLLM/Ollama/NIM, etc.). Stored locally in a
  gitignored file (<code>maelcom.auth.toml</code>, owner-only); tokens are masked
  here and never committed or sent anywhere but your own server.</p>
  <div id='authform'></div>
</div>

<script src='https://unpkg.com/@strudel/web@1.0.3'></script>
<script>
const statusEl=document.getElementById('status');
const newsEl=document.getElementById('news');
const btn=document.getElementById('play');
const modelSel=document.getElementById('model');
const stopBtn=document.getElementById('stopbtn');

// Stop/resume the whole broadcast — pauses the server refresh loop (no polling/
// TTS/LLM) and silences the audio; resuming restores both.
let broadcasting=true;
function updateStopBtn(){ stopBtn.textContent = broadcasting ? '■ Stop broadcast' : '▶ Resume broadcast'; }
async function loadBroadcast(){
  try{ broadcasting=(await (await fetch('/broadcast')).json()).broadcasting; updateStopBtn(); }catch(e){}
}
stopBtn.addEventListener('click', async ()=>{
  broadcasting=!broadcasting; updateStopBtn();
  try{ await fetch('/broadcast?on='+(broadcasting?'true':'false'), {method:'POST'}); }catch(e){}
  await pollMusic();
});

// Populate the ambient-generator dropdown and switch models on change.
async function loadModels(){
  try{
    const d=await (await fetch('/models')).json();
    // The generator selector is hidden unless enabled by config (default off).
    document.getElementById('modelwrap').style.display = d.selector ? 'inline-block' : 'none';
    modelSel.innerHTML='';
    for(const m of d.models){
      const o=document.createElement('option'); o.value=m; o.textContent=m;
      if(m===d.current) o.selected=true; modelSel.appendChild(o);
    }
  }catch(e){}
}
modelSel.addEventListener('change', async ()=>{
  try{
    await fetch('/model?name='+encodeURIComponent(modelSel.value), {method:'POST'});
    lastProgram='';           // force a re-evaluate of the new model's program
    await pollMusic();
  }catch(e){}
});

// Concert-A tuning selector (440 / 435 / 432 Hz) — retunes all notes.
const tuningSel=document.getElementById('tuning');
async function loadTunings(){
  try{
    const d=await (await fetch('/tuning')).json();
    tuningSel.innerHTML='';
    for(const t of d.tunings){
      const o=document.createElement('option'); o.value=t; o.textContent=t+' Hz';
      if(t===d.current) o.selected=true; tuningSel.appendChild(o);
    }
  }catch(e){}
}
tuningSel.addEventListener('change', async ()=>{
  try{
    await fetch('/tuning?a='+encodeURIComponent(tuningSel.value), {method:'POST'});
    lastProgram=''; await pollMusic();
  }catch(e){}
});

// Quiet mode — music only around the news, silent between.
const quietBox=document.getElementById('quiet');
async function loadQuiet(){
  try{ const d=await (await fetch('/quiet')).json(); quietBox.checked=!!d.quiet_mode; }catch(e){}
}
quietBox.addEventListener('change', async ()=>{
  try{ await fetch('/quiet?on='+(quietBox.checked?'true':'false'), {method:'POST'}); await pollMusic(); }catch(e){}
});
let started=false, lastProgram='', currentProg='', ducked=false, viz={intensity:0, band:'theta', on:false};
const newsPlayer=new Audio(); let lastNewsUrl='';

// Play the current program; while the news reads, duck the music by scaling the
// whole stack's gain (drop the fadeIn so the toggle is immediate).
async function playCurrent(){
  if(!currentProg) return;
  const base=currentProg.replace(/\.fadeIn\([0-9.]+\)\s*$/,'');
  const code=ducked?base+'.gain(0.25)':currentProg;
  // evaluate() is async; await it so a rejection is caught here (not "uncaught").
  try{ await evaluate(code); }
  catch(e){ console.error('strudel:',e); statusEl.textContent='music error: '+((e&&e.message)||e); }
}
function setDuck(on){ if(started && ducked!==on){ ducked=on; playCurrent(); } }
newsPlayer.addEventListener('play', ()=>setDuck(true));
newsPlayer.addEventListener('ended', ()=>setDuck(false));
newsPlayer.addEventListener('pause', ()=>setDuck(false));

let musicSilenced=false;
async function pollMusic(){
  try{
    const d=await (await fetch('/genmusic')).json();
    // Gate: silence when the server says not to play (broadcast stopped, or quiet).
    if(started && d.play===false){
      if(!musicSilenced){ try{ await evaluate('silence'); }catch(e){} musicSilenced=true; }
      viz.on=false;
      statusEl.textContent = broadcasting ? '● quiet · silent (music returns before the news)'
                                          : '■ broadcast stopped';
      return;
    }
    if(!d.text){ statusEl.textContent='waiting for activity…'; return; }
    viz.intensity=d.intensity; viz.band=d.brainwave_band; viz.on=started;
    // (re)start when the program changes OR the gate just re-opened after silence
    if(started && (d.text!==lastProgram || musicSilenced)){
      lastProgram=d.text; currentProg=d.text; musicSilenced=false; await playCurrent();
    }
    const ctx=(typeof getAudioContext==='function')?getAudioContext():null;
    const ac=ctx?(' · audio '+ctx.state):'';
    statusEl.textContent=(started?(ducked?'● news over music':'● on air'):'ready')+
      ' · '+d.style+' · '+d.brainwave_band+' · intensity '+d.intensity.toFixed(2)+ac;
  }catch(e){}
}
async function pollNews(){
  try{
    const d=await (await fetch('/plan')).json();
    const segs=d.segments||[]; const seen=new Set(); let html='';
    for(const s of segs){
      if(seen.has(s.title)) continue; seen.add(s.title);
      html+='<article><h2>'+(s.title||'News')+'</h2><p>'+(s.script||'')+'</p>'+
            (s.audio_url?'<audio controls src="'+s.audio_url+'"></audio>':'')+'</article>';
    }
    newsEl.innerHTML=html||'<p class="muted">No broadcast yet.</p>';
    const first=segs.find(s=>s.audio_url);
    if(started && first && first.audio_url!==lastNewsUrl){
      lastNewsUrl=first.audio_url; newsPlayer.src=first.audio_url;
      newsPlayer.play().catch(e=>console.warn('news play:',e));
    }
  }catch(e){}
}
btn.addEventListener('click', async ()=>{
  if(started) return; started=true; btn.disabled=true; btn.textContent='● On air';
  statusEl.textContent='starting…';
  try{ await initStrudel(); }
  catch(e){ console.error(e); statusEl.textContent='init error: '+((e&&e.message)||e); return; }
  // Warm up: the first evaluate can reject with "setcps is not defined" until
  // Strudel finishes registering its runtime. Retry a tiny silent pattern until
  // it succeeds, THEN play the real program.
  statusEl.textContent='warming up…';
  for(let i=0;i<80;i++){
    try{ await evaluate('setcps(0.5)\ns("~")'); break; }
    catch(e){ await new Promise(r=>setTimeout(r,80)); }
  }
  if(typeof window.samples==='function'){
    samples('github:tidalcycles/dirt-samples').catch(e=>console.warn('samples failed:',e));
  }
  await pollMusic();
  pollNews();
});
// Tabs: Player / Settings.
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
document.querySelectorAll('#tabs a').forEach(a=>a.addEventListener('click', ()=>{
  document.querySelectorAll('#tabs a').forEach(x=>x.classList.toggle('active', x===a));
  const tab=a.dataset.tab;
  document.getElementById('player-view').hidden = tab!=='player';
  document.getElementById('settings-view').hidden = tab!=='settings';
  if(tab==='settings'){ loadNewsModel(); loadAuth(); }
}));

// News-parsing model selector (Settings) — only shown when the server runs live.
const newsModelSel=document.getElementById('newsmodel');
const newsModelCustom=document.getElementById('newsmodel-custom');
async function loadNewsModel(){
  try{
    const d=await (await fetch('/news-model')).json();
    document.getElementById('newsmodel-wrap').hidden = !d.live;
    if(!d.live) return;
    newsModelSel.innerHTML='';
    for(const m of (d.models||[])){
      const o=document.createElement('option'); o.value=m; o.textContent=m;
      if(m===d.current) o.selected=true; newsModelSel.appendChild(o);
    }
    document.getElementById('newsmodel-status').textContent='current: '+esc(d.current||'');
  }catch(e){}
}
document.getElementById('newsmodel-save').addEventListener('click', async ()=>{
  const name=(newsModelCustom.value.trim())||newsModelSel.value;
  if(!name) return;
  const st=document.getElementById('newsmodel-status'); st.textContent='saving…';
  try{
    const r=await fetch('/news-model?name='+encodeURIComponent(name), {method:'POST'});
    if(!r.ok){ st.textContent='error: '+r.status; return; }
    newsModelCustom.value=''; await loadNewsModel();
  }catch(e){ st.textContent='error'; }
});
// Auto-discover the gateway's model catalogue (OpenAI-compatible /models).
document.getElementById('newsmodel-discover').addEventListener('click', async ()=>{
  const st=document.getElementById('newsmodel-status'); st.textContent='discovering…';
  try{
    const r=await fetch('/news-model/discover', {method:'POST'});
    if(!r.ok){ st.textContent='error: '+r.status; return; }
    const d=await r.json(); await loadNewsModel();
    st.textContent = d.discovered && d.discovered.length
      ? ('discovered '+d.discovered.length+' models') : 'no models returned by the gateway';
  }catch(e){ st.textContent='error'; }
});
async function loadAuth(){
  const wrap=document.getElementById('authform');
  try{
    const d=await (await fetch('/auth')).json();
    wrap.innerHTML='';
    for(const src of d.sources){
      const c=(d.config&&d.config[src])||{};
      const row=document.createElement('div'); row.className='authrow';
      row.innerHTML='<strong>'+esc(src)+'</strong> <span class="muted">'+
        (c.token_set?('· token set '+esc(c.token_hint||'')):'· no token')+'</span>'+
        '<input class="ep" placeholder="endpoint (optional)" value="'+esc(c.endpoint||'')+'">'+
        '<input class="tok" type="password" autocomplete="off" placeholder="'+
          (c.token_set?'new token (blank keeps current)':'token')+'">'+
        '<button>Save</button>';
      const btn=row.querySelector('button');
      btn.addEventListener('click', async ()=>{
        btn.disabled=true; btn.textContent='Saving…';
        const body={source:src, endpoint:row.querySelector('.ep').value, token:row.querySelector('.tok').value};
        try{ await fetch('/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
          await loadAuth();
        }catch(e){ btn.disabled=false; btn.textContent='Save'; }
      });
      wrap.appendChild(row);
    }
  }catch(e){ wrap.textContent='Could not load settings.'; }
}

loadModels(); loadTunings(); loadQuiet(); loadBroadcast(); pollMusic(); pollNews();
setInterval(pollMusic, 8000);
setInterval(pollNews, 15000);

// Incidental visualizer: bars pulsing with intensity, hue by brainwave band.
const cv=document.getElementById('viz'), ctx=cv.getContext('2d');
const HUE={delta:210, theta:260, alpha:170, beta:35, gamma:0};
function resize(){ cv.width=cv.clientWidth; cv.height=64; }
addEventListener('resize', resize); resize();
let t=0;
function draw(){
  t+=0.05; const w=cv.width, h=cv.height; ctx.clearRect(0,0,w,h);
  const n=28, hue=HUE[viz.band]??260, amp=0.12+viz.intensity*0.88;
  const speed=viz.on?(0.5+viz.intensity*2.5):0.15;
  for(let i=0;i<n;i++){
    const x=(i+0.5)/n*w;
    const v=Math.abs(Math.sin(t*speed + i*0.5));
    const bh=4+v*amp*(h-8);
    ctx.fillStyle='hsl('+hue+' 60% '+(28+v*32)+'%)';
    ctx.fillRect(x-2,(h-bh)/2,4,bh);
  }
  requestAnimationFrame(draw);
}
draw();
</script>
"""
