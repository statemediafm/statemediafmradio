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
        self.news_temperature: float | None = None  # live [llm] sampling override
        self.news_max_tokens: int | None = None  # live [llm] length override
        self.style: str = "bbc-world"  # live-selectable writing style for the news
        self.voice: str = "alan"  # live-selectable narration voice (Piper)
        # Live roster: the refresh loop reads these; the Settings tab edits them.
        self.roster: list = []  # (topic, source, cadence, headlines) entries
        self.segments: list[dict] = []  # the segment dicts behind roster (for display)

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

    # A few writing-style suggestions for the UI; the field accepts any string.
    _STYLE_SUGGESTIONS = ["bbc-world", "npr", "sports-desk", "tech-brief", "noir"]

    @app.get("/style")
    def style() -> dict:
        """The current news writing style and a few suggestions (free-form)."""
        return {"current": store.style, "suggestions": _STYLE_SUGGESTIONS}

    @app.post("/style")
    def set_style(name: str) -> dict:
        """Set the news writing style (a free-form prompt hint). Next cycle."""
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="empty style")
        store.style = name
        return {"current": store.style}

    @app.get("/voice")
    def voice() -> dict:
        """The current narration voice and the offered Piper voices."""
        from ..newsroom.tts import voice_names

        return {"current": store.voice, "voices": voice_names()}

    @app.post("/voice")
    def set_voice(name: str) -> dict:
        """Set the narration voice (a curated Piper voice). Next cycle."""
        from ..newsroom.tts import voice_names

        if name not in voice_names():
            raise HTTPException(status_code=400, detail="unknown voice")
        store.voice = name
        return {"current": store.voice}

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
            "temperature": store.news_temperature,
            "max_tokens": store.news_max_tokens,
        }

    @app.post("/news-model")
    def set_news_model(
        name: str, temperature: float | None = None, max_tokens: int | None = None
    ) -> dict:
        """Switch the news-parsing model and (optionally) its sampling knobs — any
        model the gateway serves, plus ``temperature`` / ``max_tokens``. Applies to
        the next news cycle; only meaningful when the server is running live."""
        if store.news_model is None:
            raise HTTPException(status_code=409, detail="news parsing is not live")
        if temperature is not None and not 0.0 <= temperature <= 2.0:
            raise HTTPException(status_code=400, detail="temperature must be 0..2")
        if max_tokens is not None and max_tokens <= 0:
            raise HTTPException(status_code=400, detail="max_tokens must be positive")
        store.news_temperature = temperature
        store.news_max_tokens = max_tokens
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="empty model")
        store.news_model = name
        if name not in store.news_models:
            store.news_models.append(name)  # remember a custom entry
        return {
            "current": store.news_model,
            "models": list(store.news_models),
            "temperature": store.news_temperature,
            "max_tokens": store.news_max_tokens,
        }

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

    @app.get("/sources")
    def sources() -> dict:
        """The live roster (which sources air) and the registered source kinds.
        Tokens are never included — those live in the auth tab."""
        from ..roster import source_kinds

        items = [
            {
                "index": i,
                "topic": entry[0],
                "kind": seg.get("source"),
                "every": seg.get("every", "15m"),
                "config": {k: v for k, v in seg.items() if k != "token"},
            }
            for i, (seg, entry) in enumerate(zip(store.segments, store.roster))
        ]
        return {"sources": items, "kinds": source_kinds()}

    @app.post("/sources")
    def add_source(seg: dict = Body(...)) -> dict:  # noqa: B008 (FastAPI body param)
        """Add a source to the live roster (this session only — not written to the
        config file). ``seg`` is a segment dict: a ``source`` kind plus its params
        (e.g. ``channel`` for slack, ``project`` for jira, ``repo`` for repo)."""
        from ..roster import build_segment

        if not isinstance(seg, dict) or not seg.get("source"):
            raise HTTPException(status_code=400, detail="a 'source' kind is required")
        try:
            entry = build_segment(seg, len(store.segments))
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.segments.append(dict(seg))
        store.roster.append(entry)
        return {"index": len(store.roster) - 1, "topic": entry[0]}

    @app.delete("/sources/{index}")
    def remove_source(index: int) -> dict:
        """Remove a source from the live roster by index."""
        if not 0 <= index < len(store.roster):
            raise HTTPException(status_code=404, detail="no such source")
        store.roster.pop(index)
        seg = store.segments.pop(index) if index < len(store.segments) else {}
        return {"removed": index, "topic": seg.get("topic")}

    @app.get("/llm-presets")
    def llm_presets() -> dict:
        """Quick-fill presets for the ``llm-gateway`` row (Azure, OpenRouter,
        vLLM, Ollama, NIM, …) — endpoint + example model, no credentials."""
        from ..newsroom.llm import GATEWAY_PRESETS

        return {"presets": GATEWAY_PRESETS}

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
  .authrow select{margin-left:0}
  .chip{font:inherit;font-size:.8rem;padding:.2rem .5rem;margin:.15rem .3rem .15rem 0;
        background:transparent;color:inherit;border:1px solid #bbb;border-radius:999px}
  .srcrow{display:flex;align-items:baseline;gap:.5rem;margin:.3rem 0;padding:.35rem 0;
          border-top:1px solid #eee}
  .srcrow .grow{flex:1} .srcrow .kind{font-variant:small-caps;color:#666}
  .srcrow button{margin:0;padding:.25rem .6rem;font-size:.8rem;background:transparent;
                 color:inherit;border:1px solid #bbb;border-radius:2px}
  #newsbadge{margin-left:1rem}
  @media(prefers-color-scheme:dark){
    #tabs{border-color:#333} #tabs a.active{color:#eee;border-bottom-color:#eee}
    .authrow{border-color:#333} .authrow input{background:#111;color:#eee;border-color:#444}
    .srcrow{border-color:#333} .chip,.srcrow button{border-color:#555}}
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
<span class='muted' id='newsbadge'></span>
<canvas id='viz'></canvas>
<section id='news'><p class='muted'>Loading…</p></section>
</div>
<div id='settings-view' hidden>
  <h2>Sources</h2>
  <p class='muted'>Which activity Maelcom airs. Changes apply to the running
  session (not written to the config file).</p>
  <div id='sourcelist'></div>
  <div class='authrow'>
    <select id='src-kind'></select>
    <input id='src-topic' placeholder='topic (optional)'>
    <input id='src-param' placeholder='—'>
    <input id='src-every' placeholder='every (e.g. 15m)' value='15m'>
    <input id='src-headlines' type='number' min='1' placeholder='headlines (max read)'>
    <input id='src-maxcount' type='number' min='1' placeholder='max_count (items polled)'>
    <input id='src-offset' placeholder='offset (e.g. 0, 5m)'>
    <button id='src-add'>Add source</button>
    <span class='muted' id='src-status'></span>
  </div>

  <h2>Narration</h2>
  <p class='muted'>The news writing style (a free-form prompt hint) and the
  speaking voice. Applies to the next news cycle.</p>
  <div class='authrow'>
    <label class='muted'>style
      <input id='style-input' list='style-list' placeholder='e.g. bbc-world'>
      <datalist id='style-list'></datalist>
    </label>
    <label class='muted'>voice <select id='voice-sel'></select></label>
    <button id='narration-save'>Apply</button>
    <span class='muted' id='narration-status'></span>
  </div>

  <div id='newsmodel-wrap' hidden>
    <h2>News-parsing model</h2>
    <p class='muted'>Which model on the <code>llm-gateway</code> writes the news.
    Pick one the gateway serves, or type a model string. Applies to the next news
    cycle.</p>
    <div class='authrow'>
      <select id='newsmodel'></select>
      <input id='newsmodel-custom' placeholder='or type a model, e.g. openai/gpt-4o-mini'>
      <input id='newsmodel-temp' type='number' step='0.1' min='0' max='2' placeholder='temperature'>
      <input id='newsmodel-maxtokens' type='number' min='1' placeholder='max_tokens'>
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
  <p class='muted'>Gateway presets (fill the <code>llm-gateway</code> endpoint below
  and suggest a news model — the API key still goes in its token field):</p>
  <div id='presets'></div>
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
  if(tab==='settings'){ loadSources(); loadNarration(); loadNewsModel(); loadPresets(); loadAuth(); }
}));

// ── Narration: writing style + speaking voice ────────────────────────────────
async function loadNarration(){
  try{
    const s=await (await fetch('/style')).json();
    const inp=document.getElementById('style-input'); inp.value=s.current||'';
    const dl=document.getElementById('style-list'); dl.innerHTML='';
    for(const x of (s.suggestions||[])){ const o=document.createElement('option'); o.value=x; dl.appendChild(o); }
    const v=await (await fetch('/voice')).json();
    const sel=document.getElementById('voice-sel'); sel.innerHTML='';
    for(const x of (v.voices||[])){ const o=document.createElement('option'); o.value=x; o.textContent=x;
      if(x===v.current) o.selected=true; sel.appendChild(o); }
  }catch(e){}
}
document.getElementById('narration-save').addEventListener('click', async ()=>{
  const st=document.getElementById('narration-status'); st.textContent='saving…';
  const style=document.getElementById('style-input').value.trim();
  const voice=document.getElementById('voice-sel').value;
  try{
    if(style){ const r=await fetch('/style?name='+encodeURIComponent(style),{method:'POST'});
      if(!r.ok){ st.textContent='style error'; return; } }
    if(voice){ const r=await fetch('/voice?name='+encodeURIComponent(voice),{method:'POST'});
      if(!r.ok){ st.textContent='voice error'; return; } }
    st.textContent='applied (next cycle)';
  }catch(e){ st.textContent='error'; }
});

// ── Live source management ───────────────────────────────────────────────────
// The add menu leads with GitHub/GitLab work-item URLs (both the forge `repo`
// kind — a repo URL or a pasted issue/PR/MR URL), then the other sources. Each
// option names its one extra parameter and a placeholder.
const ADD_OPTIONS=[
  {label:'GitHub work items (URL)', kind:'repo', key:'repo',
   ph:'https://github.com/owner/repo  (or an issue/PR URL)'},
  {label:'GitLab work items (URL)', kind:'repo', key:'repo',
   ph:'https://gitlab.com/group/project  (or an issue/MR URL)'},
  {label:'Hacker News', kind:'hackernews', key:null, ph:null},
  {label:'Slack channel', kind:'slack', key:'channel', ph:'channel name or ID'},
  {label:'Jira project', kind:'jira', key:'project', ph:'project key, e.g. OPS'},
  {label:'PagerDuty', kind:'pagerduty', key:'statuses',
   ph:'statuses (comma-sep), e.g. triggered,acknowledged'},
];
const srcKind=document.getElementById('src-kind');
const srcParam=document.getElementById('src-param');
let addOptions=[];  // ADD_OPTIONS plus any extra server kinds (e.g. plugins)
function currentAddOption(){ return addOptions[srcKind.value] || {}; }
async function loadSources(){
  const list=document.getElementById('sourcelist');
  try{
    const d=await (await fetch('/sources')).json();
    if(srcKind.options.length===0){
      const covered=new Set(['hn','repo','hackernews','slack','jira','pagerduty']);
      const extras=(d.kinds||[]).filter(k=>!covered.has(k)).map(k=>({label:k, kind:k, key:null, ph:null}));
      addOptions=ADD_OPTIONS.concat(extras);
      addOptions.forEach((opt,i)=>{ const o=document.createElement('option'); o.value=i; o.textContent=opt.label; srcKind.appendChild(o); });
      updateSrcPlaceholder();
    }
    list.innerHTML='';
    for(const s of (d.sources||[])){
      const row=document.createElement('div'); row.className='srcrow';
      const cfg=s.config||{};
      const extra=[cfg.headlines!=null?('headlines '+cfg.headlines):'',
                   cfg.max_count!=null?('max '+cfg.max_count):''].filter(Boolean).join(' · ');
      row.innerHTML='<span class="kind">'+esc(s.kind||'?')+'</span>'+
        '<span class="grow">'+esc(s.topic||'')+' <span class="muted">· every '+esc(s.every)+
        (extra?(' · '+esc(extra)):'')+'</span></span>'+
        '<button>Remove</button>';
      row.querySelector('button').addEventListener('click', async ()=>{
        try{ await fetch('/sources/'+s.index,{method:'DELETE'}); await loadSources(); }catch(e){}
      });
      list.appendChild(row);
    }
    if(!(d.sources||[]).length) list.innerHTML='<p class="muted">No sources yet.</p>';
  }catch(e){ list.textContent='Could not load sources.'; }
}
function updateSrcPlaceholder(){
  const opt=currentAddOption();
  srcParam.placeholder = opt.ph || '—'; srcParam.style.display = opt.key ? '' : 'none';
}
srcKind.addEventListener('change', updateSrcPlaceholder);
document.getElementById('src-add').addEventListener('click', async ()=>{
  const st=document.getElementById('src-status'); const opt=currentAddOption();
  const seg={source:opt.kind};
  if(opt.key){
    const v=srcParam.value.trim(); if(!v){ st.textContent='needs '+opt.ph; return; }
    seg[opt.key] = opt.kind==='pagerduty' ? v.split(',').map(x=>x.trim()).filter(Boolean) : v;
  }
  const val=id=>document.getElementById(id).value.trim();
  if(val('src-topic')) seg.topic=val('src-topic');
  if(val('src-every')) seg.every=val('src-every');
  if(val('src-headlines')) seg.headlines=parseInt(val('src-headlines'),10);
  if(val('src-maxcount')) seg.max_count=parseInt(val('src-maxcount'),10);
  if(val('src-offset')) seg.offset=val('src-offset');
  st.textContent='adding…';
  try{
    const r=await fetch('/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(seg)});
    if(!r.ok){ const e=await r.json().catch(()=>({})); st.textContent='error: '+(e.detail||r.status); return; }
    for(const id of ['src-param','src-topic','src-headlines','src-maxcount','src-offset'])
      document.getElementById(id).value='';
    st.textContent=''; await loadSources();
  }catch(e){ st.textContent='error'; }
});

// ── LLM gateway presets ──────────────────────────────────────────────────────
async function loadPresets(){
  const wrap=document.getElementById('presets');
  try{
    const d=await (await fetch('/llm-presets')).json();
    wrap.innerHTML='';
    for(const p of (d.presets||[])){
      const b=document.createElement('button'); b.className='chip'; b.textContent=p.name;
      b.title='endpoint '+(p.api_base||'(set yours)')+' · model '+p.model;
      b.addEventListener('click', ()=>{
        const ep=document.querySelector('.authrow[data-source="llm-gateway"] .ep');
        if(ep) ep.value=p.api_base;
        newsModelCustom.value=p.model;
        document.getElementById('newsmodel-status').textContent='preset: '+p.name+' — set the API key below, then Save.';
      });
      wrap.appendChild(b);
    }
  }catch(e){}
}

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
    document.getElementById('newsmodel-temp').value = d.temperature!=null ? d.temperature : '';
    document.getElementById('newsmodel-maxtokens').value = d.max_tokens!=null ? d.max_tokens : '';
    document.getElementById('newsmodel-status').textContent='current: '+esc(d.current||'');
  }catch(e){}
}
document.getElementById('newsmodel-save').addEventListener('click', async ()=>{
  const name=(newsModelCustom.value.trim())||newsModelSel.value;
  if(!name) return;
  const st=document.getElementById('newsmodel-status'); st.textContent='saving…';
  let q='/news-model?name='+encodeURIComponent(name);
  const t=document.getElementById('newsmodel-temp').value.trim();
  const mt=document.getElementById('newsmodel-maxtokens').value.trim();
  if(t!=='') q+='&temperature='+encodeURIComponent(t);
  if(mt!=='') q+='&max_tokens='+encodeURIComponent(mt);
  try{
    const r=await fetch(q, {method:'POST'});
    if(!r.ok){ const e=await r.json().catch(()=>({})); st.textContent='error: '+(e.detail||r.status); return; }
    newsModelCustom.value=''; await loadNewsModel(); loadNewsBadge();
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
      const row=document.createElement('div'); row.className='authrow'; row.dataset.source=src;
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

// News-parsing badge on the Player tab: live model, or the deterministic copy.
const newsBadge=document.getElementById('newsbadge');
async function loadNewsBadge(){
  try{
    const d=await (await fetch('/news-model')).json();
    newsBadge.textContent = d.live ? ('news: '+(d.current||'live model')) : 'news: offline copy';
    newsBadge.title = d.live ? 'LLM-written via the llm-gateway' : 'deterministic, no LLM';
  }catch(e){}
}

loadModels(); loadTunings(); loadQuiet(); loadBroadcast(); loadNewsBadge(); pollMusic(); pollNews();
setInterval(pollMusic, 8000);
setInterval(pollNews, 15000);
setInterval(loadNewsBadge, 30000);

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
