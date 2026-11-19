# collableton: git-based collaboration CLI for Ableton Live projects

## Problem
Two musicians share Ableton Live 12 projects (macOS). Live has no merge or locking: if both
save the same `.als`, the last save silently wins. `collableton` wraps git + Git LFS file locking so
overwrites can't happen, and preflights a set for common sharing mistakes before it's pushed.
Both run Live 12.4; one runs Live 12 Intro. Both own EZdrummer 3 (VST3).

## Constraints
- One Python 3 file, standard library only, runs on the python3 shipped with Xcode CLT (3.9:
  no `tomllib`).
- No GUI, server, daemon, file watcher, or sync layer. GitHub + Git LFS handles sync.
- Never merges `.als` files (use Live's "drag tracks in from another set").
- Rendered stems are not stored in git; keep them in a `stems/` folder outside the repo.

## Layout
- `collableton.py`: the CLI (installed onto PATH as `collableton`). Pure parsing/check/diff functions,
  separate from the git subprocess layer.
- `test_collableton.py`: `unittest`, synthetic gzipped `.als` fixtures built in code.
- `README.md`: install, the take → work → give loop, what each check means.

## Repos
- The tool repo (this one) holds only collableton itself.
- A separate private **music repo** holds the Live projects: either one project at its root, or
  one Live project folder per song. Both collaborators have write access.
- `take`/`give`/`check` act on the sets below the current folder: inside one song's folder
  that's just that song, so two people can hold different songs at once; at the repo top
  it's every song.

## `collableton.toml`
Lives at the music repo's top; a song folder may carry its own to override it (the nearest
one above a set wins).
A flat TOML subset: top-level `key = value`, values are strings, ints, or string arrays.
```toml
min_live_version = "12.4"   # oldest Live version either collaborator runs
plugin_format = "VST3"      # VST3 | VST2 | AU
plugins = ["EZdrummer 3"]   # plugins both collaborators own
```
`min_live_version` is compared at the precision written: `"12.4"` accepts any 12.4.x set;
`"12.4.16"` accepts only 12.4.16 or older.

## .als format
gzip-compressed XML, root `<Ableton Creator="Ableton Live 12.4.16">`.
- Tracks: `MidiTrack`, `AudioTrack`, `ReturnTrack`, `MainTrack`; name in
  `Name/EffectiveName@Value`; identity via `Id`.
- Plugins: `PluginDevice`/`AuPluginDevice` containing `Vst3PluginInfo`, `VstPluginInfo`, or
  `AuPluginInfo` with a `Name` (or `PlugName`) value.
- Clips: `MidiClip` with `MidiNoteEvent` notes; `AudioClip`.
- Sample/media refs: `SampleRef/FileRef` with `Path` and `RelativePath`. Device-preset refs
  (`FileRef` elsewhere) are ignored.
- Tempo: main track's `Tempo/Manual@Value`.

## Commands
- `collableton init`: git init (if needed), `git lfs install`, write `.gitattributes`
  (`*.als` lockable/binary-merge/`diff=als`, audio → LFS), `.gitignore`, `collableton.toml`;
  configure the `als` diff textconv (LFS smudge then `gzip -dc`). Idempotent; never
  overwrites existing files. Run again after cloning.
- `collableton take`: fast-forward to `origin` (never merge), then lock every `.als` in scope.
  Refuse, naming owner and time, if anyone else holds one of those locks.
- `collableton give -m MSG [--force]`: run `check` on the sets in scope (stop on errors unless
  `--force`), require that you hold the lock on every changed set in scope, commit everything
  in scope, push, unlock your locks in scope.
- `collableton check [file.als ...]`: errors exit non-zero.
  - ERROR: sample ref outside the project folder and not Core Library.
  - ERROR: plugin not in the allowlist, or in a format other than `plugin_format`.
  - WARN: MIDI track with a plugin and zero notes across its clips.
  - WARN: set saved by a Live newer than `min_live_version`.
  - WARN: more than 16 audio + MIDI tracks (Live Intro limit).
- `collableton diff [A] [B]`: semantic summary (tracks added/removed/renamed, plugins
  added/removed, per-track clip and note counts, tempo, Live version). `A`/`B` are each a
  file path or git revision; defaults are `HEAD` and the working copy.
- `collableton status`: lock holders, ahead/behind remote, last few commits.
