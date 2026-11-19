#!/usr/bin/env python3
"""riff: share Ableton Live sets over git without overwriting each other.

take -> work in Live -> give.  `riff --help` for commands.
"""
import argparse
import gzip
import json
import os
import re
import subprocess
import sys
from collections import Counter
import xml.etree.ElementTree as ET

CONFIG_FILE = "riff.toml"
DEFAULT_CONFIG = {"min_live_version": "12.4", "plugin_format": "VST3", "plugins": ["EZdrummer 3"]}
SEED_CONFIG = """\
# Shared rules for this project. Both collaborators use the same file.

# Oldest Live version either of you runs. "12.4" accepts any 12.4.x set.
min_live_version = "12.4"

# Plugin format you agreed on: "VST3", "VST2" or "AU".
plugin_format = "VST3"

# Plugins you both own. Add one only when both of you have it installed.
plugins = [
  "EZdrummer 3",
]
"""
INTRO_TRACK_LIMIT = 16
PLUGIN_FORMATS = {"Vst3PluginInfo": "VST3", "VstPluginInfo": "VST2", "AuPluginInfo": "AU"}
PLUGIN_DEVICES = ("PluginDevice", "AuPluginDevice")
TRACK_KINDS = {"MidiTrack": "MIDI", "AudioTrack": "audio", "GroupTrack": "group",
               "ReturnTrack": "return", "MainTrack": "main", "MasterTrack": "main"}
CORE_LIBRARY = re.compile(r"^/Applications/Ableton Live 12[^/]*\.app/")
AUDIO = ("wav", "aif", "aiff", "mp3", "flac", "ogg", "m4a")
GITATTRIBUTES = """\
# Live sets: kept in Git LFS, locked while someone works on them, never merged.
*.als filter=lfs diff=als merge=binary -text lockable
# Audio: kept in Git LFS.
""" + "".join(f"*.{''.join(f'[{c}{c.upper()}]' for c in ext)} filter=lfs diff=lfs merge=lfs -text\n"
              for ext in AUDIO)
GITIGNORE = """\
# Live's automatic backups stay on your own Mac.
Backup/
# Live rebuilds sample analysis files on its own.
*.asd
# Rendered stems don't go in git (see README).
stems/
# macOS and iCloud clutter. iCloud's numbered conflict copies ("Song 2.als")
# aren't ignored, because a real set can have a number in its name.
.DS_Store
Icon\r\r
*.icloud
*(conflicted copy)*
"""
# git may hand us an LFS pointer instead of the set: smudge it back (real content passes
# through, with a warning we silence), then unzip the XML.
TEXTCONV = 'f() { git lfs smudge < "$1" 2>/dev/null | gzip -dc; }; f'


class RiffError(Exception):
    """A problem to show the musician as-is (no traceback)."""


# ---------------------------------------------------------------- config

def parse_config(text):
    """Read the flat TOML subset riff.toml uses: key = "str" | 123 | ["a", "b"]."""
    # ponytail: not a real TOML parser (no tables, no single-quoted strings); tomllib needs 3.11
    cfg, decoder = {}, json.JSONDecoder()
    lines = iter(text.splitlines())
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise RiffError(f'riff.toml: expected `name = value`, got: {line}')
        value = value.strip()
        while True:
            try:
                cfg[key.strip()] = decoder.raw_decode(re.sub(r",\s*]", "]", value))[0]
                break
            except ValueError:
                more = next(lines, None) if value.startswith("[") else None
                if more is None:
                    raise RiffError(f'riff.toml: can\'t read the value of "{key.strip()}". '
                                    'Use double quotes for text, e.g. plugins = ["EZdrummer 3"]')
                if not more.strip().startswith("#"):
                    value += " " + more.split(" #")[0]
    return cfg


def load_config(start_dir):
    """Find riff.toml in start_dir or a parent; fall back to defaults."""
    d = os.path.abspath(start_dir)
    while True:
        path = os.path.join(d, CONFIG_FILE)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **parse_config(f.read())}
        parent = os.path.dirname(d)
        if parent == d:
            return dict(DEFAULT_CONFIG)
        d = parent


def version_tuple(text):
    m = re.search(r"\d+(?:\.\d+)*", text or "")
    return tuple(int(x) for x in m.group().split(".")) if m else ()


# ---------------------------------------------------------------- .als parsing (pure)

def _value(el, path):
    found = el.find(path) if el is not None else None
    return found.get("Value", "") if found is not None else ""


def read_als(data):
    """Bytes of a .als (gzipped or plain XML) -> root Element."""
    if data.startswith(b"version https://git-lfs"):
        raise RiffError("this is a Git LFS placeholder, not the real set. Run: git lfs pull")
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError):
            raise RiffError("the file is damaged (couldn't unzip it)")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise RiffError("this doesn't look like an Ableton Live set")
    if root.tag != "Ableton":
        raise RiffError("this doesn't look like an Ableton Live set")
    return root


def _track(el):
    plugins = []
    for dev in el.iter():
        if dev.tag in PLUGIN_DEVICES:
            info = next((e for e in dev.iter() if e.tag in PLUGIN_FORMATS), None)
            if info is not None:
                name = _value(info, "Name") or _value(info, "PlugName") or "(unnamed plugin)"
                plugins.append((name, PLUGIN_FORMATS[info.tag]))
    midi_clips = list(el.iter("MidiClip"))
    samples = set()
    for ref in el.iter("SampleRef"):
        fr = ref.find("FileRef")
        if fr is not None:
            samples.add((_value(fr, "Path"), _value(fr, "RelativePath")))
    return {
        "key": f'{el.tag}:{el.get("Id", "")}',
        "kind": TRACK_KINDS[el.tag],
        "name": _value(el, "Name/EffectiveName") or _value(el, "Name/UserName") or el.tag,
        "plugins": plugins,
        "clips": len(midi_clips) + sum(1 for _ in el.iter("AudioClip")),
        "notes": sum(1 for c in midi_clips for _ in c.iter("MidiNoteEvent")),
        "samples": sorted(samples),
    }


def summarize(root):
    """Root Element -> plain dict of what riff cares about."""
    live_set = root.find("LiveSet")
    if live_set is None:
        raise RiffError("this doesn't look like an Ableton Live set (no LiveSet inside)")
    tracks_el = live_set.find("Tracks")
    els = (list(tracks_el) if tracks_el is not None else []) + \
          [e for e in live_set if e.tag in ("MainTrack", "MasterTrack")]
    tracks = [_track(e) for e in els if e.tag in TRACK_KINDS]
    main = next((e for e in els if e.tag in ("MainTrack", "MasterTrack")), None)
    tempo = _value(main, ".//Tempo/Manual")
    return {
        "version": ".".join(map(str, version_tuple(root.get("Creator")))),
        "tempo": f"{float(tempo):g}" if tempo else "",
        "tracks": tracks,
    }


def load_set(path):
    with open(path, "rb") as f:
        return summarize(read_als(f.read()))


# ---------------------------------------------------------------- check (pure)

def sample_problem(path, rel, project_dir, exists=os.path.exists):
    """None if the collaborator will find this sample, else a short reason."""
    if CORE_LIBRARY.match(path):
        return None
    project = os.path.join(os.path.abspath(project_dir), "")
    if path and os.path.abspath(path).startswith(project):
        return None if exists(path) else "is missing from the project folder"
    rel = os.path.normpath(rel) if rel else ""
    if rel and not rel.startswith(("/", "..")):
        # Same project folder on the other person's Mac: the absolute path differs, the relative one doesn't.
        return None if exists(os.path.join(project_dir, rel)) else "is missing from the project folder"
    return "is outside the project folder"


def check_set(summary, cfg, project_dir, exists=os.path.exists):
    """-> list of ("ERROR" | "WARN", message). Duplicates removed, order kept."""
    out = []
    allowed = {p.casefold() for p in cfg["plugins"]}
    fmt = cfg["plugin_format"]
    for t in summary["tracks"]:
        for path, rel in t["samples"]:
            problem = sample_problem(path, rel, project_dir, exists)
            if problem:
                out.append(("ERROR", f'Track "{t["name"]}": sample "{path or rel}" {problem}. '
                                     'In Live: File > Collect All and Save.'))
        for name, pfmt in t["plugins"]:
            if name.casefold() not in allowed:
                out.append(("ERROR", f'Track "{t["name"]}": plugin "{name}" isn\'t on the shared list '
                                     f'in {CONFIG_FILE}. Swap it for one you both own, or add it '
                                     'to the list if you both have it.'))
            elif pfmt != fmt:
                out.append(("ERROR", f'Track "{t["name"]}": "{name}" is loaded as {pfmt}, but you agreed '
                                     f'on {fmt}. Replace it with the {fmt} version.'))
        if t["kind"] == "MIDI" and t["plugins"] and t["notes"] == 0:
            out.append(("WARN", f'Track "{t["name"]}" plays {t["plugins"][0][0]} but has no MIDI notes '
                                'in Live. The groove may live inside the plugin, where Live can\'t '
                                'show or edit it. Drag it into a Live clip.'))
    have, want = version_tuple(summary["version"]), version_tuple(cfg["min_live_version"])
    if have and want and have[:len(want)] > want:
        out.append(("WARN", f'Saved with Live {summary["version"]}, newer than {cfg["min_live_version"]} '
                            f'in {CONFIG_FILE}. Your collaborator may not be able to open it.'))
    n = sum(1 for t in summary["tracks"] if t["kind"] in ("MIDI", "audio"))
    if n > INTRO_TRACK_LIMIT:
        out.append(("WARN", f"{n} audio/MIDI tracks: Live Intro only allows {INTRO_TRACK_LIMIT}."))
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- diff (pure)

def _describe(t):
    bits = [t["kind"], f'{t["clips"]} clips', f'{t["notes"]} notes']
    if t["plugins"]:
        bits.append(", ".join(n for n, _ in t["plugins"]))
    return f'"{t["name"]}" ({", ".join(bits)})'


def diff_sets(a, b):
    """Two summaries -> human-readable lines. Tracks are matched by Live's track Id."""
    lines = []
    if a["version"] != b["version"]:
        lines.append(f'Live version: {a["version"]} -> {b["version"]}')
    if a["tempo"] != b["tempo"]:
        lines.append(f'Tempo: {a["tempo"]} -> {b["tempo"]} BPM')
    old = {t["key"]: t for t in a["tracks"]}
    new = {t["key"]: t for t in b["tracks"]}
    for k in old.keys() - new.keys():
        lines.append(f"- removed track {_describe(old[k])}")
    for k in [k for k in new if k not in old]:
        lines.append(f"+ added track {_describe(new[k])}")
    for k in [k for k in new if k in old]:
        o, n = old[k], new[k]
        if o["name"] != n["name"]:
            lines.append(f'~ renamed track "{o["name"]}" -> "{n["name"]}"')
        before, after = Counter(o["plugins"]), Counter(n["plugins"])
        for name, fmt in (after - before).elements():
            lines.append(f'+ "{n["name"]}": added plugin {name} ({fmt})')
        for name, fmt in (before - after).elements():
            lines.append(f'- "{n["name"]}": removed plugin {name} ({fmt})')
        if (o["clips"], o["notes"]) != (n["clips"], n["notes"]):
            lines.append(f'~ "{n["name"]}": clips {o["clips"]} -> {n["clips"]}, '
                         f'notes {o["notes"]} -> {n["notes"]}')
    return lines or ["No changes to tracks, plugins, clips, notes, tempo or Live version."]


# ---------------------------------------------------------------- git plumbing

def run(*cmd, check=True):
    """Run a command, return stdout. On failure raise RiffError with git's own words."""
    try:
        p = subprocess.run(cmd, capture_output=True)
    except FileNotFoundError:
        raise RiffError(f"{cmd[0]} isn't installed. See the README (brew install git-lfs).")
    if check and p.returncode != 0:
        msg = (p.stderr or p.stdout).decode(errors="replace").strip()
        raise RiffError(f"`{' '.join(cmd)}` failed:\n{msg}")
    return p.stdout.decode(errors="replace") if check else p


def git(*args, check=True):
    return run("git", *args, check=check)


def repo_root():
    p = git("rev-parse", "--show-toplevel", check=False)
    if p.returncode != 0:
        raise RiffError("this folder isn't shared with riff yet. Run `riff init` inside the Live project folder.")
    return p.stdout.decode().strip()


def project_sets(root):
    """All .als files in the project, skipping Live's Backup folders."""
    found = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in ("Backup", ".git")]
        found += [os.path.relpath(os.path.join(d, f), root) for f in files if f.endswith(".als")]
    return sorted(found)


def changed_sets(porcelain_z):
    """Paths of .als files in `git status --porcelain -z` output."""
    entries, out = iter(porcelain_z.split("\0")), []
    for e in entries:
        if e[:1] in ("R", "C"):
            next(entries, None)  # the rename's original path
        if e[3:].endswith(".als"):
            out.append(e[3:])
    return out


def when(locked_at):
    from datetime import datetime
    try:
        return datetime.fromisoformat(locked_at.replace("Z", "+00:00")).astimezone().strftime("%a %b %-d, %H:%M")
    except ValueError:
        return locked_at


def lock_line(lock, mine=False):
    who = "you" if mine else lock.get("owner", {}).get("name", "someone")
    return f'{lock["path"]}: taken by {who} since {when(lock.get("locked_at", ""))}'


def locks(root):
    """-> (ours, theirs), each a list of git-lfs lock dicts."""
    data = json.loads(run("git", "-C", root, "lfs", "locks", "--verify", "--json") or "{}")
    return data.get("ours") or [], data.get("theirs") or []


def require_origin(root):
    if git("-C", root, "remote", "get-url", "origin", check=False).returncode != 0:
        raise RiffError("this project isn't connected to GitHub yet. Create an empty repo there, then:\n"
                        "  git remote add origin https://github.com/YOU/SONG.git")


def branch(root):
    return git("-C", root, "branch", "--show-current").strip() or "main"


def sync(root):
    """Fast-forward to what's on GitHub. Never merges."""
    git("-C", root, "fetch", "-q", "origin")
    remote = f"origin/{branch(root)}"
    if git("-C", root, "rev-parse", "-q", "--verify", remote, check=False).returncode != 0:
        return  # nothing shared yet
    try:
        git("-C", root, "merge", "-q", "--ff-only", remote)
    except RiffError as e:
        raise RiffError(f"couldn't get the latest version from GitHub. Usually this means files you "
                        f"changed here were also changed there.\n{e}")


# ---------------------------------------------------------------- commands

def cmd_init(args):
    if git("rev-parse", "--show-toplevel", check=False).returncode != 0:
        git("init", "-q")
        print("Started a git repository here.")
    root = repo_root()
    run("git", "-C", root, "lfs", "install")
    for name, content in ((".gitattributes", GITATTRIBUTES), (".gitignore", GITIGNORE), (CONFIG_FILE, SEED_CONFIG)):
        path = os.path.join(root, name)
        if os.path.exists(path):
            print(f"Kept your existing {name}")
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"Wrote {name}")
    git("-C", root, "config", "diff.als.textconv", TEXTCONV)
    print("Ready. Next: riff take, work in Live, then riff give -m \"what you did\"")
    if git("-C", root, "remote", "get-url", "origin", check=False).returncode != 0:
        print("First connect this folder to an empty GitHub repo:\n"
              "  git remote add origin https://github.com/YOU/SONG.git")
    return 0


def cmd_take(args):
    root = repo_root()
    require_origin(root)
    sync(root)
    sets = project_sets(root)
    if not sets:
        raise RiffError("no .als files in this project yet. Save your set into this folder first.")
    ours, theirs = locks(root)
    blocked = [lock for lock in theirs if lock["path"] in sets]
    if blocked:
        for lock in blocked:
            print(lock_line(lock))
        raise RiffError("someone else is working on this. Ask them to run `riff give`, then try again.")
    mine = {lock["path"] for lock in ours}
    for path in sets:
        if path not in mine:
            run("git", "-C", root, "lfs", "lock", "--json", path)
    print("You have it: " + ", ".join(sets))
    print('Work in Live, save, then: riff give -m "what you did"')
    return 0


def cmd_give(args):
    root = repo_root()
    require_origin(root)
    if cmd_check(argparse.Namespace(files=[])) != 0:
        if not args.force:
            raise RiffError("fix the errors above, then run give again (or `riff give --force` to share anyway).")
        print("\n!!! --force: sharing a set that FAILED the check. Your collaborator may hear\n"
              "!!! something different or be unable to open it.\n")
    ours, theirs = locks(root)
    mine, others = {lock["path"] for lock in ours}, {lock["path"]: lock for lock in theirs}
    changed = changed_sets(git("-C", root, "status", "--porcelain", "-z", "--untracked-files=all"))
    for path in changed:
        if path in others:
            raise RiffError(f"{lock_line(others[path])}. Sharing your copy would overwrite their work.")
        if path not in mine:
            raise RiffError(f"you changed {path} without taking it first. Run `riff take` "
                            "(your changes are kept), then give again.")
    git("-C", root, "add", "-A")
    if git("-C", root, "diff", "--cached", "--quiet", check=False).returncode != 0:
        git("-C", root, "commit", "-q", "-m", args.message)
    else:
        print("Nothing new to share; just handing the set back.")
    try:
        git("-C", root, "push", "-q", "-u", "origin", "HEAD")
    except RiffError as e:
        raise RiffError(f"couldn't upload to GitHub. You still have the set.\n{e}")
    for lock in ours:
        run("git", "-C", root, "lfs", "unlock", "--id", str(lock["id"]))
    print("Shared and handed back. Your collaborator can now riff take.")
    return 0


def cmd_status(args):
    root = repo_root()
    online = git("-C", root, "fetch", "-q", "origin", check=False).returncode == 0
    if not online:
        print("Couldn't reach GitHub; showing what this Mac knows.")
    else:
        ours, theirs = locks(root)
        for lock in ours:
            print(lock_line(lock, mine=True))
        for lock in theirs:
            print(lock_line(lock))
        if not ours and not theirs:
            print("Nobody has taken anything.")
        remote = f"origin/{branch(root)}"
        if git("-C", root, "rev-parse", "-q", "--verify", remote, check=False).returncode == 0:
            ahead, behind = git("-C", root, "rev-list", "--left-right", "--count", f"HEAD...{remote}").split()
            if behind != "0":
                print(f"GitHub has {behind} newer version(s). riff take will get them.")
            if ahead != "0":
                print(f"{ahead} version(s) here aren't on GitHub yet.")
    dirty = git("-C", root, "status", "--porcelain").splitlines()
    if dirty:
        print(f"{len(dirty)} file(s) changed here since the last give.")
    log = git("-C", root, "log", "-5", "--format=  %h  %ar  %an: %s", check=False)
    if log.returncode == 0:
        print("Recent versions:\n" + log.stdout.decode(errors="replace").rstrip())
    return 0


def report(path, findings):
    print(f"{path}:" + ("" if findings else " OK"))
    for level, msg in findings:
        print(f"  {level}: {msg}")


def cmd_check(args):
    root = repo_root_or_cwd()
    paths = args.files or [os.path.join(root, p) for p in project_sets(root)]
    if not paths:
        raise RiffError("no .als files here.")
    errors = 0
    for path in paths:
        try:
            summary = load_set(path)
        except (RiffError, OSError) as e:
            report(path, [("ERROR", str(e))])
            errors += 1
            continue
        findings = check_set(summary, load_config(os.path.dirname(os.path.abspath(path))),
                             os.path.dirname(os.path.abspath(path)))
        report(os.path.relpath(path), findings)
        errors += sum(1 for level, _ in findings if level == "ERROR")
    return 1 if errors else 0


def repo_root_or_cwd():
    p = git("rev-parse", "--show-toplevel", check=False)
    return p.stdout.decode().strip() if p.returncode == 0 else os.getcwd()


def cmd_diff(args):
    files = [x for x in args.args if x.endswith(".als") and os.path.isfile(x)]
    revs = [x for x in args.args if x not in files]
    if len(files) == 2 and not revs:
        print(f"{files[0]} -> {files[1]}")
        for line in diff_sets(load_set(files[0]), load_set(files[1])):
            print("  " + line)
        return 0
    if len(files) > 1 or len(revs) > 2:
        raise RiffError("usage: riff diff [rev1] [rev2] [file.als]   or   riff diff a.als b.als")
    root = repo_root()
    old, new = (revs + [None, None])[:2]
    old = old or "HEAD"
    pathspec = [os.path.relpath(os.path.abspath(f), root) for f in files] or ["*.als"]
    changed = [p for p in git("-C", root, "diff", "--name-only", "-z", old, *([new] if new else []),
                              "--", *pathspec).split("\0") if p.endswith(".als")]
    if not changed:
        print(f"No sets changed between {old} and {new or 'your working copy'}.")
        return 0
    for path in changed:
        print(f"{path}  ({old} -> {new or 'working copy'})")
        a, b = _set_at(root, old, path), _set_at(root, new, path)
        if a is None or b is None:
            print("  " + ("new set" if a is None else "set deleted"))
            continue
        for line in diff_sets(a, b):
            print("  " + line)
    return 0


def _set_at(root, rev, path):
    """Summary of `path` at `rev` (None = working copy), or None if it doesn't exist there."""
    if rev is None:
        full = os.path.join(root, path)
        return load_set(full) if os.path.isfile(full) else None
    p = git("-C", root, "cat-file", "--filters", f"{rev}:{path}", check=False)
    return summarize(read_als(p.stdout)) if p.returncode == 0 else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="riff", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    sub.add_parser("init", help="set up this Live project folder for sharing").set_defaults(fn=cmd_init)
    sub.add_parser("take", help="get the latest version and lock it so only you can change it").set_defaults(fn=cmd_take)
    p = sub.add_parser("give", help="check, share your changes, and unlock")
    p.add_argument("-m", "--message", required=True, help="what you changed")
    p.add_argument("--force", action="store_true", help="share even if the check finds errors")
    p.set_defaults(fn=cmd_give)
    sub.add_parser("status", help="who has the set, and what's new").set_defaults(fn=cmd_status)
    p = sub.add_parser("check", help="look for sharing mistakes in a set")
    p.add_argument("files", nargs="*", metavar="file.als")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("diff", help="what changed between two versions of a set")
    p.add_argument("args", nargs="*", metavar="rev|file.als")
    p.set_defaults(fn=cmd_diff)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except RiffError as e:
        sys.stdout.flush()
        print(f"riff: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
