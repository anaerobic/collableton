# collableton: notes for coding agents

collableton is a small CLI that lets two musicians share Ableton Live projects over GitHub
without overwriting each other: Git LFS file locks (`take`/`give`) plus a preflight `check` and
a semantic `diff` of `.als` sets. This repo holds only the tool. Music lives in each user's own,
separate music repo; never add Live projects or audio here.

## Layout

- `collableton.py`: the whole CLI. The top half is pure functions (parse `.als` → summary,
  `check_set`, `diff_sets`); the bottom half is the git/LFS subprocess layer and the commands.
  Keep the two apart: parsing and checks must stay testable without git.
- `test_collableton.py`: `unittest` suite. Fixtures are small synthetic `.als` sets built in
  code (gzip of a minimal XML string); never commit real project files as fixtures.
- `.agents/skills/collableton/SKILL.md`: the agent skill (Agent Skills format) that teaches an
  agent to run collableton for musicians. `.claude/skills/collableton` is a symlink to it.
- `.superpowers/specs/`: the design spec.

## Constraints

- Standard library only, and it must run on the `python3` that ships with Xcode Command Line
  Tools (3.9). That rules out `tomllib`, `X | None` type syntax and `match`. `collableton.toml`
  is read by a small parser for a flat TOML subset.
- Output is read by musicians, not developers: plain words, and say what to do in Live.
- Never merge `.als` files. `take` and `give` may only fast-forward or replay the user's own
  unshared commits; when the same file changed on both sides, stop without touching anything.

## Testing

```sh
/usr/bin/python3 -m unittest -v test_collableton
```

Run it with the stock 3.9 interpreter so newer syntax is caught. The git tests need `git-lfs`.
LFS locking can't run against `file://` remotes, so tests fake the lock lookups (`fake_locks`);
real locking has to be checked against GitHub with two accounts.

## Live behavior the code relies on

Verified against Live 12.4 saves:

- Live saves over read-only sets without a warning (it replaces the file), so read-only is
  no protection. `give` refusing changed sets you don't hold the lock on is the safeguard.
- A new project's `Ableton Project Info` folder is empty, and git drops empty folders. Live
  treats a folder as a Project only if it has one, so `init` and `take` recreate it.
- Clips in Live's Groove Pool are `MidiClip`s too, but not on a track. Count clips per track.
- A plugin's own state (e.g. an EZdrummer groove) lives in an opaque `ProcessorState` blob,
  not in Live clips.
- Device-preset `FileRef`s are not samples. Only `SampleRef/FileRef` counts.

## When you change behavior

The skill quotes collableton's messages in its troubleshooting table. When you change a
message or a command's behavior, update `.agents/skills/collableton/SKILL.md` and the README in
the same change.
