---
name: collableton
description: Run the collableton CLI for musicians who share Ableton Live projects over git, and explain what it reports. Use this whenever someone works in a music repo that has a collableton.toml, or asks to take, lock, give, share, push or hand back a song or Live set; asks who has a set, what changed between versions, or why `collableton check` or `give` failed; says Live won't save a set or a file is read-only; or wants to set up or join a shared Ableton project on GitHub. Use it even when they don't name collableton ("send my changes to Pat", "can I work on the song now?", "what did they change in the bridge?").
---

# collableton

collableton lets two people share Ableton Live projects in a git repo without overwriting each
other. Live can't merge two edits of the same `.als`, so collableton keeps one person per set:
`take` locks a song (Git LFS file lock on GitHub), `give` checks it, commits, pushes and
unlocks it. The people asking are usually musicians, not developers. Talk about songs, sets,
plugins and samples, not refs, rebases or LFS pointers, unless they ask.

## The loop

Run commands from inside the song's folder (for example `our-music/Song A Project`).
`take` and `give` act on the folder you're in: inside one song's folder they cover just that
song, so both people can work on different songs at once. At the top of the music repo they
cover every song.

```sh
collableton status                    # who has what, is GitHub ahead, recent versions
collableton take                      # get the latest and lock this song
# ...they work in Live, save, close the set...
collableton give -m "what changed"    # check, commit, push, unlock
```

- Run `collableton status` first when you're not sure of the state.
- Have them `take` a song before opening it in Live, not after. Sets are read-only until taken,
  but Live saves over read-only sets without a warning, so read-only stops nothing; `give` is
  what catches an edit to a set they never took.
- Remind them to save and close the set in Live before `give`. A set that's still open can get
  saved again after it's shared.
- For the give message, describe what changed in the music ("added a Serum bassline, tempo
  120 → 124"). `collableton diff` gives you the facts. Ask if you can't tell what they did.

## Rules that protect their work

These exist because the failure they prevent is someone losing hours of music:

- **Use collableton for sharing, not raw git.** `git commit`/`git push` on a set skip the lock and
  sample checks, and pushing someone's lock-free edit is exactly the overwrite collableton
  prevents. Use raw git read-only: `git log`, `git status`.
- **Never use `give --force` on your own initiative.** It shares a set that failed its check, so
  the other person may get missing samples or plugins. Explain the error and let the person
  decide. If they choose `--force`, say what their collaborator will run into.
- **Never break or steal a lock** (`git lfs unlock --force`, deleting locks on GitHub). If the
  other person has the song, say who and since when, and suggest they ask that person to
  `collableton give`.
- **Never edit `.als` files** (gunzip, XML edits, scripts). Live is the only safe editor; a
  hand-edited set can fail to open or silently lose parts. Fixes happen in Live.
- **`collableton.toml` is an agreement between two people.** Before adding a plugin to the
  list, confirm that both of them own that plugin. Otherwise the check is just switched off.
- **Never merge two versions of a set.** To combine work, open one set and drag tracks in from
  the other through Live's browser.

## When `check` or `give` reports a problem

`give` runs `check` first. Any ERROR stops it and nothing gets shared. WARNs don't stop
anything. Translate each message into what to do in Live:

| Message contains | What it means | What to tell them |
|---|---|---|
| `is outside the project folder` | The set uses an audio file from elsewhere on this Mac (their library, Downloads, another project). The other person doesn't have it. | In Live: **File > Collect All and Save**, then give again. Live asks which files to copy; besides the defaults, set *Files from Factory Packs* to Yes (the other person may not have that pack, and `check` flags pack samples too). The files land in the project's `Samples/` folder. |
| `is missing from the project folder, or git won't share it` | The file is gone, or sits somewhere git ignores (like `stems/`). | Same fix: **Collect All and Save**. If they deleted the file, they need to restore or replace it in Live. |
| `isn't on the shared list` | A plugin the other person may not own. | Swap it for a plugin you both own. Or, if both own it, add it to `collableton.toml` (confirm that first). |
| `is loaded as AU` / `VST2` | The agreed plugin, but in the wrong format; on the other Mac it may load as missing. | In Live, replace the device with the VST3 version (or whatever `plugin_format` says). |
| `has no MIDI notes in Live` (WARN) | Usually a drum plugin (e.g. EZdrummer) playing its own internal groove. Live can't show or edit those notes. | Drag the groove from the plugin into a Live MIDI clip. Optional: the groove is shared either way, inside the plugin's saved settings, but only a Live clip can be seen and edited in Live. |
| `refers to "…" inside its own settings` (WARN) | A plugin saved a path to an audio file in its own data (e.g. audio loaded into EZdrummer to build a groove). Collect All doesn't see it, so the other person won't have the file. | If the plugin needs it, copy the file into the project folder and load it from there inside the plugin. If the plugin only used it once (e.g. to analyze it), it can be ignored. |
| `newer than … in collableton.toml` (WARN) | Saved with a newer Live than the other person runs. | They should check their Live versions match. Once both have updated, raise `min_live_version`. |
| `Live Intro only allows 16` (WARN) | Too many tracks for someone on Live Intro. | Get down to 16 audio/MIDI tracks before handing over (freeze and flatten, or bounce tracks together). |

You can't press buttons in Live, so give exact menu steps and offer to re-run `check` once
they've done it. Fix only what the message names; don't reorganize their project.

## Other errors

- **`taken by NAME since …`**: someone else has that song. Say so plainly; nothing is wrong
  locally.
- **`you changed X without taking it first`**: they edited a set they hadn't taken. Their edits are
  still there. Run `collableton take`: if nobody else has the song, it locks it and keeps the
  changes, and then `give` works. If someone else has it, both people changed the same set.
  Don't pick a winner: suggest keeping their version via *Save Live Set As* under a new name,
  then combining by dragging tracks in later.
- **`couldn't combine your copy with the latest from GitHub`**: the same file changed in both
  places. Nothing was lost. Show `git status` and tell them what clashed. Don't run `git
  reset`, `git checkout --`, or `git stash drop`; those can discard work. Ask before doing
  anything else.
- **`couldn't upload to GitHub`**: usually a network or GitHub sign-in problem. They still have
  the lock. Retry once; if it fails again, show the message.
- **Live says the set is "outside of a Project folder"** and wants Save As: the song's
  `Ableton Project Info` folder is missing. It's often empty, and git doesn't keep empty
  folders, so fresh clones lose it. Run `collableton take` (or `collableton init`), which puts
  it back, then save again. Don't let them Save As somewhere else: that splits the song.
- **The file is read-only**: the set isn't taken. Run `collableton take`. Live itself saves over
  read-only sets anyway, so if they've already edited and saved, `take` keeps their changes
  as long as nobody else has the song.
- **A plugin's window doesn't open when they click its track**: that's normal. Live opens a plugin
  window automatically only when the plugin is added. To open it later, select the track and click
  the wrench icon on the plugin's title bar in the Device View.
- **`Git LFS placeholder`**: the real set wasn't downloaded. Run `git lfs pull`.
- **`isn't connected to GitHub yet`** or **`isn't shared with collableton yet`**: setup isn't
  finished; see below.

## Seeing what changed

`collableton diff` prints a short summary: tracks added, removed or renamed, plugins, clip and
note counts per track, tempo, and Live version.

- `collableton diff`: the working copy against the last shared version.
- `collableton diff HEAD~1 HEAD`: the last two shared versions. Use `git log --oneline` to find
  older ones.
- `collableton diff a.als b.als`: two set files directly.

Pass the summary on in plain words. Don't show raw XML: `git diff` on a set is unreadable.

## Setup

Install, once per Mac:

```sh
brew install git-lfs
git clone https://github.com/anaerobic/collableton.git ~/collableton
sudo mkdir -p /usr/local/bin && sudo ln -sf ~/collableton/collableton.py /usr/local/bin/collableton
```

The music lives in a separate private GitHub repo, not in the collableton repo. To create one,
one person:

1. Creates an empty private GitHub repo and adds the other person as a collaborator.
2. Moves their Live project folders into one folder, then runs:
   `collableton init`, `git remote add origin <url>`, `collableton take`,
   `collableton give -m "First version"`.

To join, the other person runs `git clone <url>`, then `collableton init` inside the clone.

Run `collableton init` again after every fresh clone; it never overwrites existing files. To
start a new song, they use *File > Save Live Set As* into a new folder in the music repo, then
take and give from inside that folder.

Rendered stems don't go in git (GitHub's large-file storage fills up quickly). `stems/` is
ignored; share stems some other way.
