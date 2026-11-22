import binascii
import contextlib
import gzip
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import collableton

# ---------------------------------------------------------------- tiny synthetic .als builders


def plugin(name, info="Vst3PluginInfo", device="PluginDevice", state="3q2+7w=="):
    tag = "PlugName" if info == "VstPluginInfo" else "Name"
    return (f'<{device} Id="9"><PluginDesc><{info} Id="0"><{tag} Value="{name}"/></{info}></PluginDesc>'
            f'<ProcessorState>{state}</ProcessorState></' + device + '>')


def midi_clip(notes):
    events = '<MidiNoteEvent Time="0" Duration="1" Velocity="100"/>' * notes
    return (f'<MidiClip Id="0" Time="0"><CurrentEnd Value="16"/><Notes><KeyTracks><KeyTrack>'
            f'<Notes>{events}</Notes></KeyTrack></KeyTracks></Notes></MidiClip>')


def sample(path, rel=""):
    return (f'<AudioClip Id="0" Time="0"><SampleRef><FileRef><RelativePath Value="{rel}"/>'
            f'<Path Value="{path}"/></FileRef></SampleRef></AudioClip>')


PRESET_REF = ('<LastPresetRef><Value><FilePresetRef Id="0"><FileRef>'
              '<Path Value="/Users/pma/Library/Application Support/Ableton/Simpler.adv"/>'
              '</FileRef></FilePresetRef></Value></LastPresetRef>')


def track(tag, id, name, body=""):
    return (f'<{tag} Id="{id}"><Name><EffectiveName Value="{name}"/><UserName Value=""/></Name>'
            f'<DeviceChain>{body}</DeviceChain></{tag}>')


def als(*tracks, creator="Ableton Live 12.4.16", tempo="120"):
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><Ableton MajorVersion="5" Creator="{creator}">'
           f'<LiveSet><Tracks>{"".join(tracks)}</Tracks><MainTrack><DeviceChain><Mixer><Tempo>'
           f'<Manual Value="{tempo}"/></Tempo></Mixer></DeviceChain></MainTrack></LiveSet></Ableton>')
    return gzip.compress(xml.encode())


def summary(*tracks, **kw):
    return collableton.summarize(collableton.read_als(als(*tracks, **kw)))


CFG = dict(collableton.DEFAULT_CONFIG)
EZ = track("MidiTrack", 1, "Drums", plugin("EZdrummer 3") + midi_clip(8))


def findings(*tracks, cfg=CFG, project="/proj", exists=lambda p: True, **kw):
    return collableton.check_set(summary(*tracks, **kw), cfg, project, exists)


def riff_problem(path, rel, project):
    return collableton.sample_problem(path, rel, project, os.path.exists)


def levels(*tracks, **kw):
    return [level for level, _ in findings(*tracks, **kw)]


# ---------------------------------------------------------------- tests


class ConfigTest(unittest.TestCase):
    def test_flat_values(self):
        cfg = collableton.parse_config('# shared\nmin_live_version = "12.4"  # oldest\nplugins = ["A", "B"]\nn = 3\n')
        self.assertEqual(cfg, {"min_live_version": "12.4", "plugins": ["A", "B"], "n": 3})

    def test_multiline_array_with_comments_and_trailing_comma(self):
        cfg = collableton.parse_config('plugins = [\n  "EZdrummer 3",  # drums\n  # "Serum",\n  "Valhalla",\n]\n')
        self.assertEqual(cfg["plugins"], ["EZdrummer 3", "Valhalla"])

    def test_bad_value_is_friendly(self):
        with self.assertRaisesRegex(collableton.CollabletonError, "double quotes"):
            collableton.parse_config("plugins = ['EZdrummer 3']")

    def test_seed_config_round_trips(self):
        self.assertEqual(collableton.parse_config(collableton.SEED_CONFIG), collableton.DEFAULT_CONFIG)


class ReadTest(unittest.TestCase):
    def test_parses_tracks_plugins_clips_tempo_version(self):
        s = summary(EZ, track("AudioTrack", 2, "Vox", sample("/proj/Samples/v.wav")), tempo="96.5")
        self.assertEqual(s["version"], "12.4.16")
        self.assertEqual(s["tempo"], "96.5")
        drums, vox, main = s["tracks"]
        self.assertEqual((drums["name"], drums["kind"], drums["clips"], drums["notes"]), ("Drums", "MIDI", 1, 8))
        self.assertEqual(drums["plugins"], [("EZdrummer 3", "VST3")])
        self.assertEqual((vox["kind"], vox["clips"], vox["samples"]), ("audio", 1, [("/proj/Samples/v.wav", "")]))
        self.assertEqual(main["kind"], "main")

    def test_plugin_formats(self):
        s = summary(track("MidiTrack", 1, "x", plugin("A", "VstPluginInfo") + plugin("B", "AuPluginInfo", "AuPluginDevice")))
        self.assertEqual(s["tracks"][0]["plugins"], [("A", "VST2"), ("B", "AU")])

    def test_preset_refs_are_not_samples(self):
        self.assertEqual(summary(track("MidiTrack", 1, "x", PRESET_REF))["tracks"][0]["samples"], [])

    def test_plain_xml_is_accepted(self):
        self.assertEqual(collableton.read_als(gzip.decompress(als())).tag, "Ableton")

    def test_lfs_pointer_is_explained(self):
        with self.assertRaisesRegex(collableton.CollabletonError, "git lfs pull"):
            collableton.read_als(b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n")

    def test_not_a_set(self):
        for junk in (b"hello", gzip.compress(b"<Other/>"), b"\x1f\x8bnope"):
            with self.assertRaises(collableton.CollabletonError):
                collableton.read_als(junk)


class CheckTest(unittest.TestCase):
    def test_clean_set(self):
        self.assertEqual(findings(EZ), [])

    def test_sample_outside_project_is_error(self):
        f = findings(track("AudioTrack", 1, "Vox", sample("/Users/pma/Library/kick.wav", "../../Library/kick.wav")))
        self.assertEqual(f[0][0], "ERROR")
        self.assertIn("Collect All and Save", f[0][1])

    def test_core_library_and_project_samples_are_ok(self):
        core = "/Applications/Ableton Live 12 Intro.app/Contents/App-Resources/Core Library/Samples/k.wav"
        self.assertEqual(levels(track("AudioTrack", 1, "a", sample(core) + sample("/proj/Samples/a.wav"))), [])

    def test_project_sample_saved_on_the_other_mac_is_ok(self):
        t = track("AudioTrack", 1, "a", sample("/Users/pma/Music/Song/Samples/a.wav", "Samples/a.wav"))
        self.assertEqual(levels(t, exists=lambda p: p == "/proj/Samples/a.wav"), [])

    def test_project_sample_not_on_disk_is_error(self):
        f = findings(track("AudioTrack", 1, "a", sample("/proj/Samples/a.wav")), exists=lambda p: False)
        self.assertIn("missing", f[0][1])

    def test_sample_linked_from_outside_the_project_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            proj, outside = os.path.join(d, "proj"), os.path.join(d, "kick.wav")
            os.mkdir(proj)
            open(outside, "wb").close()
            os.symlink(outside, os.path.join(proj, "kick.wav"))
            self.assertEqual(riff_problem(os.path.join(proj, "kick.wav"), "", proj), "is outside the project folder")
            self.assertEqual(riff_problem("/elsewhere/kick.wav", "kick.wav", proj), "is outside the project folder")

    def test_audio_path_saved_inside_a_plugin_warns(self):
        def ez(payload):
            hexed = binascii.hexlify(payload).decode()
            state = "\n\t" + "\n\t".join(hexed[i:i + 64] for i in range(0, len(hexed), 64))  # as Live writes it
            return track("MidiTrack", 1, "Drums", plugin("EZdrummer 3", state=state) + midi_clip(8))
        f = findings(ez(b'Tempo 60 InputAudio "/Users/pat/Desktop/doodle.aif" { Events 1 2 3'))
        self.assertEqual([l for l, _ in f], ["WARN"])
        self.assertIn('"/Users/pat/Desktop/doodle.aif" inside its own settings', f[0][1])
        library = b'Library "/Library/Application Support/Toontrack/EZdrummer/Kit.wav" Drums "/Volumes/SSD/Toontrack/'
        self.assertEqual(levels(ez(library)), [])
        self.assertEqual(levels(ez(b'InputAudio "/proj/Samples/doodle.aif"')), [])  # inside the project

    def test_device_preset_refs_ignored(self):
        self.assertEqual(levels(track("MidiTrack", 1, "x", PRESET_REF + midi_clip(1))), [])

    def test_unlisted_plugin_is_error(self):
        f = findings(track("MidiTrack", 1, "Bass", plugin("Serum") + midi_clip(4)))
        self.assertEqual(f[0][0], "ERROR")
        self.assertIn('"Serum"', f[0][1])

    def test_allowlist_ignores_case(self):
        self.assertEqual(levels(track("MidiTrack", 1, "x", plugin("ezdrummer 3") + midi_clip(1))), [])

    def test_wrong_format_is_error(self):
        for info, device in (("VstPluginInfo", "PluginDevice"), ("AuPluginInfo", "AuPluginDevice")):
            f = findings(track("MidiTrack", 1, "Drums", plugin("EZdrummer 3", info, device) + midi_clip(1)))
            self.assertEqual(f[0][0], "ERROR")
            self.assertIn("agreed on VST3", f[0][1])

    def test_plugin_with_no_notes_warns(self):
        for body in (midi_clip(0), ""):
            f = findings(track("MidiTrack", 1, "Drums", plugin("EZdrummer 3") + body))
            self.assertEqual(f[0][0], "WARN")
            self.assertIn("inside the plugin", f[0][1])

    def test_newer_live_warns(self):
        self.assertEqual(levels(EZ, creator="Ableton Live 12.5.1"), ["WARN"])
        self.assertEqual(levels(EZ, creator="Ableton Live 12.4.99"), [])
        self.assertEqual(levels(EZ, creator="Ableton Live 12.4.17", cfg={**CFG, "min_live_version": "12.4.16"}), ["WARN"])
        self.assertEqual(levels(EZ, creator="Ableton Live 12.3"), [])

    def test_intro_track_limit(self):
        def n(k):
            return [track("AudioTrack", i, f"t{i}") for i in range(k)] + [track("ReturnTrack", 99, "A-Reverb")]
        self.assertEqual(levels(*n(16)), [])
        self.assertEqual(levels(*n(17)), ["WARN"])

    def test_duplicates_collapsed(self):
        t = track("MidiTrack", 1, "x", plugin("Serum") + plugin("Serum") + midi_clip(1))
        self.assertEqual(levels(t), ["ERROR"])


class DiffTest(unittest.TestCase):
    def diff(self, a, b):
        return collableton.diff_sets(a, b)

    def test_no_changes(self):
        self.assertIn("No changes", self.diff(summary(EZ), summary(EZ))[0])

    def test_version_and_tempo(self):
        out = self.diff(summary(EZ), summary(EZ, creator="Ableton Live 12.4.17", tempo="124"))
        self.assertEqual(out, ["Live version: 12.4.16 -> 12.4.17", "Tempo: 120 -> 124 BPM"])

    def test_tracks_added_removed_renamed(self):
        vox = track("AudioTrack", 2, "Vox")
        bass = track("MidiTrack", 3, "Bass", midi_clip(5))
        out = self.diff(summary(EZ, vox), summary(track("MidiTrack", 1, "Kit", plugin("EZdrummer 3") + midi_clip(8)), bass))
        self.assertEqual(out, ['- removed track "Vox" (audio, 0 clips, 0 notes)',
                               '+ added track "Bass" (MIDI, 1 clips, 5 notes)',
                               '~ renamed track "Drums" -> "Kit"'])

    def test_plugins_added_removed(self):
        before = summary(track("MidiTrack", 1, "Drums", plugin("EZdrummer 3") + plugin("Old Comp") + midi_clip(8)))
        after = summary(track("MidiTrack", 1, "Drums", plugin("EZdrummer 3") + plugin("Valhalla") + midi_clip(8)))
        self.assertEqual(self.diff(before, after), ['+ "Drums": added plugin Valhalla (VST3)',
                                                    '- "Drums": removed plugin Old Comp (VST3)'])

    def test_settings_changed_inside_a_plugin(self):
        after = summary(track("MidiTrack", 1, "Drums", plugin("EZdrummer 3", state="AAAAgroove") + midi_clip(8)))
        self.assertEqual(self.diff(summary(EZ), after),
                         ['~ "Drums": EZdrummer 3 settings changed inside the plugin (Live can\'t show what)'])

    def test_ezdrummer_grooves_changed(self):
        def ez_plugin(*parts, kit=b"Post-Rock", sig=b"4 4"):
            # Shaped like EZdrummer 3's saved song: one MidiLoop per part, each hit is 3 numbers.
            # Offset is in beats from the song's start: here each part is 8 bars of 4/4.
            text = b'PlugVersion 3.1.2\nsoundlib "%s"\nSongTrack 0 {\nTimeSig %s 0\n' % (kit, sig) + b"".join(
                b'MidiLoop %d {\nName "%s"\nOffset %d\nTimeSig %s\nMidiEvents { \nEndTime 32\nEvents %s\n}\n}\n'
                % (i, name.encode(), 32 * i, sig, b"6235280 0 0.13 " * hits) for i, (name, hits) in enumerate(parts))
            hexed = binascii.hexlify(b"VstW\x00\x00\x00\x08" + text).decode()
            state = "\n\t" + "\n\t".join(hexed[i:i + 64] for i in range(0, len(hexed), 64))  # as Live writes it
            return plugin("EZdrummer 3", state=state)

        def ez(*parts, **kw):
            return summary(track("MidiTrack", 1, "Drums", ez_plugin(*parts, **kw) + midi_clip(0)))
        before = ez(("Build-Up I 01", 120), ("Build-Up I 01", 120), ("Verse 02", 194))
        after = ez(("Build-Up I 01", 120), ("Breakdown 04", 168), ("Verse 02", 200), ("Fill 01", 12))
        self.assertEqual(self.diff(before, after), [
            '~ "Drums": EZdrummer 3 part 2 (bar 9): Build-Up I 01, 120 hits -> Breakdown 04, 168 hits',
            '~ "Drums": EZdrummer 3 part 3 (bar 17): Verse 02, 194 hits -> Verse 02, 200 hits',
            '+ "Drums": EZdrummer 3 part 4 (bar 25): Fill 01, 12 hits'])
        self.assertEqual(self.diff(after, before)[-1], '- "Drums": EZdrummer 3 part 4 (bar 25): Fill 01, 12 hits')
        # Only 4/4 has been checked against EZdrummer's bar numbers, so other meters show no bar.
        self.assertEqual(self.diff(ez(("Fill 01", 12), sig=b"6 8"), ez(("Fill 02", 14), sig=b"6 8")),
                         ['~ "Drums": EZdrummer 3 part 1: Fill 01, 12 hits -> Fill 02, 14 hits'])
        # Same parts, another setting changed (here the kit): all we can say is that something changed.
        self.assertEqual(self.diff(before, ez(("Build-Up I 01", 120), ("Build-Up I 01", 120), ("Verse 02", 194),
                                              kit=b"Modern")),
                         ['~ "Drums": EZdrummer 3 settings changed inside the plugin (Live can\'t show what)'])
        # Two EZdrummers on one track (e.g. in a rack): each is compared with the one in the same place.
        def two(first):
            return summary(track("MidiTrack", 1, "Drums", ez_plugin(first) + ez_plugin(("Verse 02", 194))))
        self.assertEqual(self.diff(two(("Fill 01", 12)), two(("Fill 02", 14))),
                         ['~ "Drums": EZdrummer 3 #1 part 1 (bar 1): Fill 01, 12 hits -> Fill 02, 14 hits'])
        # Swapping two instances' settings is a change in each place, not "no changes".
        def pair(a, b):
            return summary(track("MidiTrack", 1, "Drums", ez_plugin(a) + ez_plugin(b)))
        self.assertEqual(self.diff(pair(("Fill 01", 12), ("Fill 02", 14)), pair(("Fill 02", 14), ("Fill 01", 12))),
                         ['~ "Drums": EZdrummer 3 #1 part 1 (bar 1): Fill 01, 12 hits -> Fill 02, 14 hits',
                          '~ "Drums": EZdrummer 3 #2 part 1 (bar 1): Fill 02, 14 hits -> Fill 01, 12 hits'])

    def test_clip_and_note_counts(self):
        after = summary(track("MidiTrack", 1, "Drums", plugin("EZdrummer 3") + midi_clip(8) + midi_clip(16)))
        self.assertEqual(self.diff(summary(EZ), after), ['~ "Drums": clips 1 -> 2, notes 8 -> 24'])


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.dir.name)
        self.addCleanup(os.chdir, self.cwd)

    def write(self, name, data):
        if os.path.exists(name):
            os.chmod(name, 0o644)  # LFS makes committed, unlocked sets read-only
        with open(name, "wb") as f:
            f.write(data)
        return name

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = collableton.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_check_exit_codes(self):
        self.write("good.als", als(EZ))
        self.write("bad.als", als(track("MidiTrack", 1, "Bass", plugin("Serum") + midi_clip(1))))
        self.assertEqual(self.cli("check", "good.als")[0], 0)
        code, out = self.cli("check", "bad.als")
        self.assertEqual(code, 1)
        self.assertIn("Serum", out)

    def test_check_uses_project_collableton_toml(self):
        self.write("collableton.toml", b'plugins = ["EZdrummer 3", "Serum"]\n')
        self.write("bad.als", als(track("MidiTrack", 1, "Bass", plugin("Serum") + midi_clip(1))))
        self.assertEqual(self.cli("check")[0], 0)

    def test_check_skips_backup_folder(self):
        os.mkdir("Backup")
        self.write("Backup/old.als", b"junk")
        self.write("song.als", als(EZ))
        code, out = self.cli("check")
        self.assertEqual(code, 0)
        self.assertNotIn("old.als", out)

    def test_diff_two_files(self):
        self.write("a.als", als(EZ))
        self.write("b.als", als(EZ, tempo="90"))
        code, out = self.cli("diff", "a.als", "b.als")
        self.assertEqual(code, 0)
        self.assertIn("Tempo: 120 -> 90 BPM", out)


class GitHelpersTest(unittest.TestCase):
    def test_changed_sets(self):
        out = "?? new.als\0 M Song.als\0R  b.als\0a.als\0 M Samples/x.wav\0"
        self.assertEqual(collableton.changed_sets(out), ["new.als", "Song.als", "b.als"])

    def test_lock_line(self):
        lock = {"id": "7", "path": "Song.als", "owner": {"name": "pma"}, "locked_at": "2026-11-18T21:03:44Z"}
        self.assertRegex(collableton.lock_line(lock), r"^Song\.als: taken by pma since \w{3} Nov 1[89], \d\d:\d\d$")
        self.assertIn("taken by you", collableton.lock_line(lock, mine=True))


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


@unittest.skipUnless(shutil.which("git-lfs"), "needs git-lfs (brew install git-lfs)")
class GitFlowTest(CliTest):
    """Real git + LFS against a local bare remote. LFS locking itself needs a real server
    (GitHub), so lock lookups are faked here."""

    def setUp(self):
        super().setUp()
        home = os.path.join(self.dir.name, "home")
        os.mkdir(home)
        env = {"HOME": home, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.remote = os.path.join(self.dir.name, "remote.git")
        sh("git", "init", "-q", "--bare", self.remote)
        os.mkdir("proj")
        os.chdir("proj")
        self.assertEqual(self.cli("init")[0], 0)

    def commit(self, msg):
        sh("git", "add", "-A")
        sh("git", "commit", "-q", "-m", msg)

    def fake_locks(self, ours=(), theirs=()):
        return mock.patch.object(collableton, "locks", return_value=(list(ours), list(theirs)))

    def test_init_writes_setup_once(self):
        with open(".gitattributes") as f:
            self.assertIn("*.als filter=lfs diff=als merge=binary -text lockable", f.read())
        self.assertEqual(sh("git", "config", "diff.als.textconv").strip(), collableton.TEXTCONV)
        self.assertTrue(os.path.isfile(".gitignore") and os.path.isfile("collableton.toml"))
        self.write("collableton.toml", b'plugins = ["Serum"]\n')
        code, out = self.cli("init")
        self.assertEqual(out.count("Kept your existing"), 5)
        with open("collableton.toml") as f:
            self.assertIn("Serum", f.read())

    def test_sets_and_audio_go_to_lfs_and_backups_are_ignored(self):
        os.makedirs("Backup")
        os.makedirs("Samples")
        self.write("Song.als", als(EZ))
        self.write("Samples/Kick.WAV", b"RIFF")
        self.write("Backup/Song [2026-11-18].als", als(EZ))
        self.commit("v1")
        self.assertEqual(sorted(sh("git", "lfs", "ls-files", "-n").split("\n")[:-1]), ["Samples/Kick.WAV", "Song.als"])
        self.assertNotIn("Backup", sh("git", "ls-files"))

    def test_git_diff_shows_set_as_xml(self):
        self.write("Song.als", als(EZ))
        self.commit("v1")
        self.write("Song.als", als(EZ, tempo="128"))
        out = sh("git", "diff")
        self.assertIn('+<?xml', out)
        self.assertIn('<Manual Value="128"/>', out)

    def test_collableton_diff_revisions_and_working_copy(self):
        self.write("Song.als", als(EZ))
        self.commit("v1")
        self.write("Song.als", als(EZ, tempo="90"))
        self.commit("v2")
        self.assertIn("Tempo: 120 -> 90 BPM", self.cli("diff", "HEAD~1", "HEAD")[1])
        self.assertIn("No sets changed", self.cli("diff")[1])
        self.write("Song.als", als(EZ, track("AudioTrack", 2, "Vox"), tempo="90"))
        self.assertIn('+ added track "Vox"', self.cli("diff", "Song.als")[1])
        self.write("Other.als", als())
        self.commit("v3")
        self.assertIn("new set", self.cli("diff", "HEAD~1", "HEAD")[1])

    def test_take_needs_a_remote(self):
        code, out = self.cli("take")
        self.assertEqual(code, 1)
        self.assertIn("isn't connected to GitHub", out)

    def test_take_refuses_when_someone_else_has_it(self):
        sh("git", "remote", "add", "origin", self.remote)
        self.write("Song.als", als(EZ))
        lock = {"id": "1", "path": "Song.als", "owner": {"name": "pma"}, "locked_at": "2026-11-18T21:03:44Z"}
        with self.fake_locks(theirs=[lock]):
            code, out = self.cli("take")
        self.assertEqual(code, 1)
        self.assertIn("Song.als: taken by pma since", out)

    def test_give_refuses_untaken_or_their_set(self):
        sh("git", "remote", "add", "origin", self.remote)
        self.write("Song.als", als(EZ))
        with self.fake_locks():
            code, out = self.cli("give", "-m", "x")
        self.assertEqual(code, 1)
        self.assertIn("without taking it first", out)
        with self.fake_locks(theirs=[{"id": "1", "path": "Song.als", "owner": {"name": "pma"}}]):
            code, out = self.cli("give", "-m", "x")
        self.assertIn("would overwrite their work", out)

    def test_give_stops_on_check_errors_unless_forced(self):
        sh("git", "remote", "add", "origin", self.remote)
        self.write("Song.als", als(track("MidiTrack", 1, "Bass", plugin("Serum") + midi_clip(1))))
        with self.fake_locks():
            code, out = self.cli("give", "-m", "x")
            self.assertEqual(code, 1)
            self.assertIn("fix the errors above", out)
            code, out = self.cli("give", "-m", "x", "--force")
        self.assertIn("!!! --force", out)
        self.assertIn("without taking it first", out)  # --force never skips the lock check

    def test_take_open_opens_the_set_only_after_locking_it(self):
        sh("git", "remote", "add", "origin", self.remote)
        os.makedirs("Song A Project")
        os.makedirs("Song B Project")
        self.write("Song A Project/A.als", als(EZ))
        self.write("Song B Project/B.als", als(EZ))
        self.commit("two songs")
        real_run, calls, open_code = collableton.run, [], 0

        def run(*cmd, **kw):
            if cmd[0] == "open":
                calls.append(("open", os.path.relpath(cmd[-1])))
                return mock.Mock(returncode=open_code)
            if cmd[3:5] == ("lfs", "lock"):
                return calls.append(("lock", cmd[-1])) or ""
            return real_run(*cmd, **kw)

        os.chdir("Song A Project")
        with self.fake_locks(), mock.patch.object(collableton, "run", run):
            self.assertEqual(self.cli("take", "--open")[0], 0)
        self.assertEqual(calls, [("lock", "Song A Project/A.als"), ("open", "A.als")])
        open_code = 1  # Live didn't open: take still succeeded, so say the song is yours
        with self.fake_locks(ours=[{"id": "1", "path": "Song A Project/A.als"}]), \
                mock.patch.object(collableton, "run", run):
            code, out = self.cli("take", "--open")
        self.assertEqual(code, 0, out)
        self.assertIn("The song is still yours", out)
        del calls[2:]

        theirs = [{"id": "1", "path": "Song A Project/A.als", "owner": {"name": "pma"},
                   "locked_at": "2026-11-18T21:03:44Z"}]
        with self.fake_locks(theirs=theirs), mock.patch.object(collableton, "run", run):
            self.assertEqual(self.cli("take", "--open")[0], 1)
        self.assertEqual(len(calls), 2)  # someone else has it: Live stays closed

        os.chdir("..")  # every song at once: Live shows one set at a time, so open none
        with self.fake_locks(), mock.patch.object(collableton, "run", run):
            code, out = self.cli("take", "--open")
        self.assertEqual(code, 0, out)
        self.assertNotIn("open", [c[0] for c in calls[2:]])
        self.assertIn("more than one set", out)

    def test_take_and_give_act_on_the_song_folder_you_are_in(self):
        sh("git", "remote", "add", "origin", self.remote)
        os.makedirs("Song A Project")
        os.makedirs("Song B Project")
        self.write("Song A Project/A.als", als(EZ))
        self.write("Song B Project/B.als", als(EZ))
        self.commit("two songs")
        real_run, calls = collableton.run, []

        def run(*cmd, **kw):
            if cmd[3:5] in (("lfs", "lock"), ("lfs", "unlock")):
                return calls.append((cmd[4], cmd[-1])) or ""
            return real_run(*cmd, **kw)

        os.chdir("Song A Project")
        with self.fake_locks(), mock.patch.object(collableton, "run", run):
            self.assertEqual(self.cli("take")[0], 0)
        self.assertEqual(calls, [("lock", "Song A Project/A.als")])

        self.write("A.als", als(EZ, tempo="99"))
        self.write("../Song B Project/B.als", als(EZ, tempo="77"))
        self.write("../collableton.toml", collableton.SEED_CONFIG.replace('"EZdrummer 3",', '"EZdrummer 3", "Serum",').encode())
        ours = [{"id": "1", "path": "Song A Project/A.als"}, {"id": "2", "path": "Song B Project/B.als"}]
        with self.fake_locks(ours=ours), mock.patch.object(collableton, "run", run):
            code, out = self.cli("give", "-m", "A only")
        self.assertEqual(code, 0, out)
        self.assertEqual(calls[1:], [("unlock", "1")])
        # The shared rules file travels with any song's give; the other song stays put.
        self.assertEqual(sh("git", "show", "--name-only", "--format=", "HEAD").split("\n")[:-1],
                         ["Song A Project/A.als", "collableton.toml"])
        self.assertIn("Song B Project/B.als", sh("git", "status", "--porcelain"))

    def no_lock_calls(self):
        real_run = collableton.run

        def run(*cmd, **kw):
            return "" if cmd[3:5] in (("lfs", "lock"), ("lfs", "unlock")) else real_run(*cmd, **kw)
        return mock.patch.object(collableton, "run", run)

    def test_two_people_giving_different_songs_both_get_shared(self):
        sh("git", "remote", "add", "origin", self.remote)
        for song in ("A", "B"):
            os.makedirs(f"Song {song}")
            self.write(f"Song {song}/{song}.als", als(EZ))
        self.commit("two songs")
        sh("git", "push", "-q", "-u", "origin", "HEAD")
        me = os.getcwd()
        sh("git", "clone", "-q", self.remote, "../other")
        os.chdir("../other")
        self.cli("init")
        them = os.getcwd()

        with self.no_lock_calls(), self.fake_locks(ours=[{"id": "1", "path": "Song A/A.als"},
                                                          {"id": "2", "path": "Song B/B.als"}]):
            os.chdir(os.path.join(me, "Song A"))
            self.write("A.als", als(EZ, tempo="101"))
            os.chdir(os.path.join(them, "Song B"))
            self.write("B.als", als(EZ, tempo="102"))
            os.chdir(os.path.join(me, "Song A"))
            self.assertEqual(self.cli("give", "-m", "A")[0], 0)
            os.chdir(os.path.join(them, "Song B"))
            code, out = self.cli("give", "-m", "B")  # GitHub moved on meanwhile
            self.assertEqual(code, 0, out)
            os.chdir(os.path.join(me, "Song A"))
            self.assertEqual(self.cli("take")[0], 0)
        self.assertEqual(sh("git", "log", "--format=%s", "-3").splitlines(), ["B", "A", "two songs"])
        self.assertEqual(collableton.load_set(os.path.join(me, "Song B/B.als"))["tempo"], "102")

    def test_ignored_sample_fails_check(self):
        os.makedirs("stems")
        self.write("stems/kick.wav", b"RIFF")
        self.write("Song.als", als(track("AudioTrack", 1, "Kick", sample(os.path.join(os.getcwd(), "stems/kick.wav")))))
        code, out = self.cli("check")
        self.assertEqual(code, 1)
        self.assertIn("git won't share it", out)

    def test_init_writes_agent_rules_for_the_music_repo(self):
        with open("AGENTS.md") as f:
            self.assertIn("Don't `git commit` or `git push` sets", f.read())
        with open("CLAUDE.md") as f:
            self.assertEqual(f.read(), "@AGENTS.md\n")
        self.write("AGENTS.md", b"# Our band rules\nBe nice.\n")
        self.write("CLAUDE.md", b"Use British spelling.\n")
        out = self.cli("init")[1]
        self.assertIn("Added collableton's rules to your AGENTS.md", out)
        with open("AGENTS.md") as f:
            text = f.read()
        self.assertTrue(text.startswith("# Our band rules") and "collableton take" in text)
        with open("CLAUDE.md") as f:
            self.assertIn("@AGENTS.md", f.read())

    def test_init_adds_missing_rules_to_existing_files(self):
        self.write(".gitattributes", b"*.wav filter=lfs diff=lfs merge=lfs -text\n")
        self.write(".gitignore", b"*.tmp\n")
        code, out = self.cli("init")
        self.assertEqual(out.count("Added collableton's rules"), 2)
        self.assertIn("lockable: set", sh("git", "check-attr", "lockable", "--", "Song.als"))
        self.assertEqual(subprocess.run(["git", "check-ignore", "-q", "Backup/x.als"]).returncode, 0)
        self.assertEqual(self.cli("init")[1].count("Kept your existing"), 5)

    def test_take_stops_untouched_when_your_edit_clashes_with_github(self):
        sh("git", "remote", "add", "origin", self.remote)
        self.write("Song.als", als(EZ))
        self.commit("v1")
        sh("git", "push", "-q", "-u", "origin", "HEAD")
        me = os.getcwd()
        sh("git", "clone", "-q", self.remote, "../other")
        os.chdir("../other")
        self.cli("init")
        self.write("Song.als", als(EZ, tempo="90"))
        self.commit("theirs")
        sh("git", "push", "-q")
        os.chdir(me)
        mine = als(EZ, tempo="140")
        self.write("Song.als", mine)  # edited without taking it
        with self.fake_locks():
            code, out = self.cli("take")
        self.assertEqual(code, 1)
        self.assertIn("newer version of them is on GitHub", out)
        with open("Song.als", "rb") as f:
            self.assertEqual(f.read(), mine)
        self.assertEqual(sh("git", "stash", "list"), "")
        self.assertEqual(sh("git", "log", "--format=%s"), "v1\n")

    def test_init_completes_partial_setup(self):
        self.write(".gitattributes", b"*.als filter=lfs lockable\n")
        self.write(".gitignore", b"Backup/\n")
        code, out = self.cli("init")
        self.assertEqual(out.count("Added collableton's rules"), 2)
        self.assertIn("merge: binary", sh("git", "check-attr", "merge", "--", "Song.als"))
        self.assertEqual(subprocess.run(["git", "check-ignore", "-q", "stems/kick.wav"]).returncode, 0)

    def test_clone_gets_back_its_empty_project_info_folder(self):
        sh("git", "remote", "add", "origin", self.remote)
        os.makedirs("Song Project/Ableton Project Info")  # empty, as Live 12 leaves it
        self.write("Song Project/Song.als", als(EZ))
        self.commit("v1")
        sh("git", "push", "-q", "-u", "origin", "HEAD")
        sh("git", "clone", "-q", self.remote, "../other")
        os.chdir("../other")
        self.assertFalse(os.path.isdir("Song Project/Ableton Project Info"))  # git dropped it
        self.cli("init")
        self.assertTrue(os.path.isdir("Song Project/Ableton Project Info"))
        os.rmdir("Song Project/Ableton Project Info")
        with self.fake_locks(), self.no_lock_calls():
            os.chdir("Song Project")
            self.assertEqual(self.cli("take")[0], 0)
        self.assertTrue(os.path.isdir("Ableton Project Info"))

    def test_give_commits_pushes_and_unlocks(self):
        sh("git", "remote", "add", "origin", self.remote)
        self.write("Song.als", als(EZ))
        real_run, unlocked = collableton.run, []

        def run(*cmd, **kw):
            if cmd[3:5] == ("lfs", "unlock"):
                return unlocked.append(cmd[-1]) or ""
            return real_run(*cmd, **kw)

        with self.fake_locks(ours=[{"id": "42", "path": "Song.als"}]), mock.patch.object(collableton, "run", run):
            code, out = self.cli("give", "-m", "First groove")
        self.assertEqual(code, 0, out)
        self.assertEqual(unlocked, ["42"])
        self.assertIn("First groove", sh("git", "--git-dir", self.remote, "log", "--format=%s"))


if __name__ == "__main__":
    unittest.main()
