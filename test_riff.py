import contextlib
import gzip
import io
import os
import tempfile
import unittest

import riff

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
    return riff.summarize(riff.read_als(als(*tracks, **kw)))


CFG = dict(riff.DEFAULT_CONFIG)
EZ = track("MidiTrack", 1, "Drums", plugin("EZdrummer 3") + midi_clip(8))


def findings(*tracks, cfg=CFG, project="/proj", exists=lambda p: True, **kw):
    return riff.check_set(summary(*tracks, **kw), cfg, project, exists)


def levels(*tracks, **kw):
    return [level for level, _ in findings(*tracks, **kw)]


# ---------------------------------------------------------------- tests


class ConfigTest(unittest.TestCase):
    def test_flat_values(self):
        cfg = riff.parse_config('# shared\nmin_live_version = "12.4"  # oldest\nplugins = ["A", "B"]\nn = 3\n')
        self.assertEqual(cfg, {"min_live_version": "12.4", "plugins": ["A", "B"], "n": 3})

    def test_multiline_array_with_comments_and_trailing_comma(self):
        cfg = riff.parse_config('plugins = [\n  "EZdrummer 3",  # drums\n  # "Serum",\n  "Valhalla",\n]\n')
        self.assertEqual(cfg["plugins"], ["EZdrummer 3", "Valhalla"])

    def test_bad_value_is_friendly(self):
        with self.assertRaisesRegex(riff.RiffError, "double quotes"):
            riff.parse_config("plugins = ['EZdrummer 3']")

    def test_seed_config_round_trips(self):
        self.assertEqual(riff.parse_config(riff.SEED_CONFIG), riff.DEFAULT_CONFIG)


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
        self.assertEqual(riff.read_als(gzip.decompress(als())).tag, "Ableton")

    def test_lfs_pointer_is_explained(self):
        with self.assertRaisesRegex(riff.RiffError, "git lfs pull"):
            riff.read_als(b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n")

    def test_not_a_set(self):
        for junk in (b"hello", gzip.compress(b"<Other/>"), b"\x1f\x8bnope"):
            with self.assertRaises(riff.RiffError):
                riff.read_als(junk)


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
        return riff.diff_sets(a, b)

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
        with open(name, "wb") as f:
            f.write(data)
        return name

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = riff.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_check_exit_codes(self):
        self.write("good.als", als(EZ))
        self.write("bad.als", als(track("MidiTrack", 1, "Bass", plugin("Serum") + midi_clip(1))))
        self.assertEqual(self.cli("check", "good.als")[0], 0)
        code, out = self.cli("check", "bad.als")
        self.assertEqual(code, 1)
        self.assertIn("Serum", out)

    def test_check_uses_project_riff_toml(self):
        self.write("riff.toml", b'plugins = ["EZdrummer 3", "Serum"]\n')
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


if __name__ == "__main__":
    unittest.main()
