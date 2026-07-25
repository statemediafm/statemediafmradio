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
        self.last_signal = None  # last ActivitySignal, for immediate model switches

    def set_plan(self, plan: BroadcastPlan) -> None:
        self.plan = plan
        self.audio = {s.audio.id: s.audio for s in plan.segments if s.audio}

    def set_program(self, program: StrudelProgram) -> None:
        self.program = program


def create_app(state: _State | None = None):
    """Build the FastAPI application. Call ``app.state.store.set_plan(...)`` to
    publish a plan for the page and API to serve."""
    from fastapi import FastAPI, HTTPException, Response
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
            return {"text": None}
        return program_to_dict(store.program)

    @app.get("/models")
    def models() -> dict:
        """The user-selectable ambient generators and the current selection."""
        from ..genmusic.styles import AMBIENT_MODELS

        return {"models": list(AMBIENT_MODELS), "current": store.model}

    @app.post("/model")
    def set_model(name: str) -> dict:
        """Switch the ambient generator; recompose immediately if we have a signal."""
        from ..genmusic import compose
        from ..genmusic.styles import STYLES

        if name not in STYLES:
            raise HTTPException(status_code=400, detail="unknown model")
        store.model = name
        if store.last_signal is not None:
            store.set_program(compose(store.last_signal, style=name))
        return {"current": store.model}

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
  #modelwrap{display:inline-block;margin-left:1rem}
  select{font:inherit;font-size:.85rem;margin-left:.35rem}
  #viz{display:block;width:100%;height:64px;margin:.5rem 0}
  article{border-top:1px solid #ccc;padding-top:.6rem;margin-top:1rem}
  audio{width:100%;margin:.4rem 0}
  @media(prefers-color-scheme:dark){
    body{background:#111;color:#eee}.muted{color:#aaa}
    button{background:#eee;color:#111}article{border-color:#333}}
</style>
<h1>Maelcom</h1>
<p class='muted' id='status'>internal radio · press play to begin</p>
<button id='play'>▶ Start radio</button>
<label class='muted' id='modelwrap'>ambient generator
  <select id='model'></select>
</label>
<canvas id='viz'></canvas>
<section id='news'><p class='muted'>Loading…</p></section>

<script src='https://unpkg.com/@strudel/web@1.0.3'></script>
<script>
const statusEl=document.getElementById('status');
const newsEl=document.getElementById('news');
const btn=document.getElementById('play');
const modelSel=document.getElementById('model');

// Populate the ambient-generator dropdown and switch models on change.
async function loadModels(){
  try{
    const d=await (await fetch('/models')).json();
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

async function pollMusic(){
  try{
    const d=await (await fetch('/genmusic')).json();
    if(!d.text){ statusEl.textContent='waiting for activity…'; return; }
    viz.intensity=d.intensity; viz.band=d.brainwave_band; viz.on=started;
    if(started && d.text!==lastProgram){ lastProgram=d.text; currentProg=d.text; await playCurrent(); }
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
loadModels(); pollMusic(); pollNews();
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
