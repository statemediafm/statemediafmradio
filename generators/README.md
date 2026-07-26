# Ambient generators — add your own

State Media FM ships two built-in ambient generators (`Entrainment 0.1`, `ScratchPad`).
You can add more **without touching the package**: drop a small TOML file in a
generators directory and point your config at it.

## Enable a generators directory

In your `--config` file:

```toml
[genmusic]
generator = "Entrainment 0.1"   # the default generator to use
selector  = false               # show the generator dropdown in the UI? (default off)
generators = "generators"       # directory of generator configs to load (this one)
```

`statemediafm serve --config your.toml` will load every `*.toml` in that directory
(files named `template.toml` or starting with `_` are skipped) and register the
**playable** ones as selectable generators.

## A generator config

Copy `template.toml` and edit it. Fields:

| field | meaning |
|---|---|
| `name` | how it appears (and how `generator = "…"` / the dropdown refers to it) |
| `description` | one line shown in listings |
| `prompt` | the composition rules / aesthetic in prose — the design spec (the same kind of rule base the built-ins were grown from, and the hook for a future prompt-driven renderer) |
| `renderer` | *optional* `"package.module:function"` of a `render(signal, intensity, band, fade_ms) -> str` (Strudel source). **With** a renderer the generator is playable and selectable; **without** one it is a design spec only (loaded, not registered). |
| `[params]` | *optional* free-form parameters your renderer may read |

## Writing a renderer

A renderer is any Python callable with the signature

```python
def render(signal, intensity, band, fade_ms=2000) -> str: ...
```

returning a self-contained Strudel program (see `statemediafm/genmusic/styles/` for
the built-ins). Put your module on the Python path and reference it, e.g.
`renderer = "my_generators.glacial:render"`.

The `prompt` is the place to write the generator's rule base first (in prose) —
then implement `render` to it, exactly how `Entrainment 0.1` and `ScratchPad`
were grown. See `ENTRAINMENT.md` for a worked example of that rule-base style.
