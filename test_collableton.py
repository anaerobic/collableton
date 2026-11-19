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


def plugin(name, info="Vst3PluginInfo", device="PluginDevice"):
    tag = "PlugName" if info == "VstPluginInfo" else "Name"
    return (f'<{device} Id="9"><PluginDesc><{info} Id="0"><{tag} Value="{name}"/></{info}></PluginDesc>'
            '<ProcessorState>3q2+7w==</ProcessorState></' + device + '>')


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
        self.assertEqual(out.count("Kept your existing"), 3)
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
        ours = [{"id": "1", "path": "Song A Project/A.als"}, {"id": "2", "path": "Song B Project/B.als"}]
        with self.fake_locks(ours=ours), mock.patch.object(collableton, "run", run):
            code, out = self.cli("give", "-m", "A only")
        self.assertEqual(code, 0, out)
        self.assertEqual(calls[1:], [("unlock", "1")])
        self.assertEqual(sh("git", "show", "--name-only", "--format=", "HEAD").split("\n")[:-1], ["Song A Project/A.als"])
        self.assertIn("Song B Project/B.als", sh("git", "status", "--porcelain"))

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
