# riff

Share Ableton Live projects over GitHub without overwriting each other's work.

Live can't merge two versions of a set: if you both save `Song.als`, the last save wins and
the other person's work is gone. riff stops that from happening. Only one of you can have a
set at a time ("take" it), and riff checks the set for common sharing mistakes before you
hand it back ("give").

## Install (once per Mac)

```sh
brew install git-lfs
sudo install -m 755 riff.py /usr/local/bin/riff
```

riff uses the `python3` that comes with Xcode Command Line Tools (`xcode-select --install`).

## Start sharing a project

One of you, inside the Live project folder (the one with the `.als` file and
`Ableton Project Info`):

```sh
riff init
git remote add origin https://github.com/YOU/SONG.git   # an empty GitHub repo
riff take
riff give -m "First version"
```

The other person:

```sh
git clone https://github.com/YOU/SONG.git
cd SONG && riff init
```

## Every session

```sh
riff take                       # get the latest version and lock it
# ...open the set in Live, make music, save...
riff give -m "added a bassline" # check it, share it, unlock it
```

Close the set in Live before you `give`.

Your sets stay read-only until you `riff take` them. If Live won't save, you probably
haven't taken the set.

## Other commands

- `riff status`: who has the set, whether GitHub has something newer, and the last few
  versions.
- `riff diff`: what changed in your set since your last give. `riff diff HEAD~1 HEAD`
  compares the last two shared versions, and `riff diff a.als b.als` compares two files.
  You get a short summary (tracks, plugins, clips, notes, tempo, Live version), not XML.
- `riff check`: runs the checks below without sharing anything.

## What `riff check` looks for

`give` runs these checks first. An **ERROR** stops the give. `riff give --force` shares the
set anyway, but your collaborator may hear something different. A **WARN** is printed and
doesn't stop anything.

- **ERROR: sample is outside the project folder / missing from the project folder.** The set
  uses an audio file the other person doesn't have. In Live, choose *File > Collect All and
  Save*, then give again. Samples from Live's built-in Core Library are fine.
- **ERROR: plugin isn't on the shared list.** The other person may not own it. Use a
  plugin you both have, or add it to `riff.toml` once you've both installed it.
- **ERROR: plugin is loaded as AU/VST2, but you agreed on VST3.** The same plugin in another
  format can show up as missing on the other Mac. Swap in the agreed format.
- **WARN: plugin but no MIDI notes in Live.** For example, an EZdrummer track whose groove
  lives inside EZdrummer's own song creator. Live can't show or edit those notes. Drag the
  groove from the plugin into a Live clip.
- **WARN: saved with a newer Live.** The set was saved by a Live newer than
  `min_live_version`, so the other person may not be able to open it.
- **WARN: more than 16 audio/MIDI tracks.** Live Intro can't have more than 16.

## `riff.toml`

`riff init` creates this file. Keep it the same for both of you, since it gets shared like
everything else.

```toml
min_live_version = "12.4"   # oldest Live either of you runs; "12.4" accepts any 12.4.x
plugin_format = "VST3"      # "VST3", "VST2" or "AU"
plugins = [
  "EZdrummer 3",            # plugins you both own
]
```

## What riff doesn't do

- **Merge sets.** If you both changed a set, open one of them and drag the tracks in from
  the other using Live's browser.
- **Store rendered stems.** GitHub's storage for large files fills up fast. Bounce stems into
  a `stems/` folder outside the project (or anywhere `.gitignore` covers) and share them
  some other way.
- **Back up Live's `Backup/` folder.** Live's automatic backups stay on your own Mac.

## Development

```sh
python3 -m unittest -v test_riff
```

The tests build small fake sets in code and need `git-lfs` for the git tests. File locking
only works against a real server (GitHub), so the tests fake the lock lookups.
