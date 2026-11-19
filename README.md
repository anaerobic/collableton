# collableton

Share Ableton Live projects over GitHub without overwriting each other's work.

Live can't merge two versions of a set: if you both save `Song.als`, the last save wins and
the other person's work is gone. collableton stops that from happening. Only one of you can
have a set at a time ("take" it), and collableton checks the set for common sharing mistakes
before you hand it back ("give").

## Two repos

- **This repo** holds the `collableton` tool. You install it once and never put music in it.
- **Your music repo** is a separate, private GitHub repo that holds your Live projects. It can
  hold one song, or a folder per song:

  ```
  our-music/
    collableton.toml        shared rules (see below)
    Song A Project/
      Song A.als
      Samples/
      Ableton Project Info/
    Song B Project/
      ...
  ```

## 1. Install collableton (each of you, once per Mac)

```sh
brew install git-lfs
git clone https://github.com/anaerobic/collableton.git ~/collableton
sudo mkdir -p /usr/local/bin
sudo ln -sf ~/collableton/collableton.py /usr/local/bin/collableton
collableton --help
```

collableton runs on the `python3` that
comes with Xcode Command Line Tools (`xcode-select --install`). To update it later, run
`git -C ~/collableton pull`.

## 2. Create the music repo (one of you, once)

1. On GitHub, create an empty **private** repo, for example `our-music`. Under
   *Settings > Collaborators*, add the other person.
2. Make a folder for it, move your Live project folders into it, and set it up:

   ```sh
   cd ~/Music/our-music
   collableton init
   git remote add origin https://github.com/YOU/our-music.git
   collableton take
   collableton give -m "First version"
   ```

## 3. Join the music repo (the other person, once)

```sh
git clone https://github.com/YOU/our-music.git ~/Music/our-music
cd ~/Music/our-music
collableton init
```

Run `collableton init` after every fresh clone. It's safe to run again: it never
overwrites files that already exist.

## Every session

```sh
cd ~/Music/our-music/"Song A Project"
collableton take                        # get the latest version and lock this song
# ...open the set in Live, make music, save, close it...
collableton give -m "added a bassline"  # check it, share it, unlock it
```

- `take` and `give` act on the folder you're in. Inside a song's folder they lock and share
  just that song, so the two of you can work on different songs at once. At the top of the
  music repo they cover every song.
- Take a song **before** you open it in Live. Sets are read-only until you take them, but Live
  saves over read-only sets without a warning, so nothing stops you editing a song you haven't
  taken. What protects your collaborator's work is `give`: it refuses to share a set you never
  took.
- If Live says a set is "outside of a Project folder", run `collableton take` (or
  `collableton init`). It puts back the song's empty `Ableton Project Info` folder, which git
  doesn't keep.
- `take` refuses if the other person has the song, and tells you who has it and since when.
- `give` refuses to share a set you changed without taking it, so nobody's work gets
  overwritten.
- To start a new song, use *File > Save Live Set As* to save it into a new folder in the music
  repo. Then `cd` into that folder and run `take` and `give`.

## Other commands

- `collableton status`: who has which song, whether GitHub has something newer, and the
  last few versions.
- `collableton diff`: what changed since the last give. `collableton diff HEAD~1 HEAD`
  compares the last two shared versions, and `collableton diff a.als b.als` compares two
  files. You get a short summary (tracks, plugins, clips, notes, tempo, Live version), not
  XML.
- `collableton check`: runs the checks below on the sets in this folder, without sharing
  anything.

## What `collableton check` looks for

`give` runs these checks first. An **ERROR** stops the give. `collableton give --force`
shares the set anyway, but your collaborator may hear something different. A **WARN** is
printed and doesn't stop anything.

- **ERROR: sample is outside the project folder / missing from the project folder.** The set
  uses an audio file the other person doesn't have. In Live, choose *File > Collect All and
  Save*, then give again. In the dialog that asks which files to copy, also set *Files from
  Factory Packs* to Yes: the other person may not have the same packs. Samples from Live's
  built-in Core Library are fine.
- **ERROR: plugin isn't on the shared list.** The other person may not own it. Use a
  plugin you both have, or add it to `collableton.toml` once you've both installed it.
- **ERROR: plugin is loaded as AU/VST2, but you agreed on VST3.** The same plugin in another
  format can show up as missing on the other Mac. Swap in the agreed format.
- **WARN: plugin but no MIDI notes in Live.** For example, an EZdrummer track whose groove
  lives inside EZdrummer's own song creator. Live can't show or edit those notes. Drag the
  groove from the plugin into a Live clip. The groove is still shared either way: it's saved
  inside the plugin's settings, which travel with the set.
- **WARN: a plugin refers to an audio file inside its own settings.** For example, audio you
  loaded into EZdrummer to build a groove from. Collect All can't copy those files, so your
  collaborator won't have them. If the plugin needs the file, copy it into the project folder
  and load it from there inside the plugin.
- **WARN: saved with a newer Live.** The set was saved by a Live newer than
  `min_live_version`, so the other person may not be able to open it.
- **WARN: more than 16 audio/MIDI tracks.** Live Intro can't have more than 16.

## `collableton.toml`

`collableton init` creates this file at the top of the music repo. It's shared like
everything else, so you both use the same rules. A song folder can have its own
`collableton.toml` to override the shared one.

```toml
min_live_version = "12.4"   # oldest Live either of you runs; "12.4" accepts any 12.4.x
plugin_format = "VST3"      # "VST3", "VST2" or "AU"
plugins = [
  "EZdrummer 3",            # plugins you both own
]
```

## What collableton doesn't do

- **Merge sets.** If you both changed a set, open one of them and drag the tracks in from
  the other using Live's browser.
- **Store rendered stems.** GitHub's storage for large files fills up fast. Bounce stems into
  a `stems/` folder (`.gitignore` skips it) or outside the music repo, and share them some
  other way.
- **Share Live's `Backup/` folders.** Live's automatic backups stay on your own Mac.

## Using it with an AI assistant

`.agents/skills/collableton` is an agent skill (the shared `SKILL.md` format) that teaches
Claude Code, Codex or GitHub Copilot to run collableton for you. It covers taking and giving
songs, explaining what `check` reports and how to fix it in Live, and summarizing what changed.
It also keeps the assistant from working around the safety rails: no raw `git push`, no
breaking the other person's lock, no `give --force` without asking, no editing `.als` files
directly.

Install it for yourself so it works in your music repo. Both of you should do this:

```sh
mkdir -p ~/.claude/skills ~/.agents/skills
ln -sf ~/collableton/.agents/skills/collableton ~/.claude/skills/collableton   # Claude Code
ln -sf ~/collableton/.agents/skills/collableton ~/.agents/skills/collableton   # Codex, Copilot
```

Because those are links, `git -C ~/collableton pull` updates the skill too. Then open your
assistant in the music repo and ask in plain words, like "take Song A for me" or "why won't my
give go through?".

## Development

```sh
python3 -m unittest -v test_collableton
```

The tests build small fake sets in code and need `git-lfs` for the git tests. File locking
only works against a real server (GitHub), so the tests fake the lock lookups.
