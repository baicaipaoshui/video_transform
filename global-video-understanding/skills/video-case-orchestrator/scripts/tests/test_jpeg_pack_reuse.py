"""NO-LLM regressions for selected-tier JPEG reuse and atomic persistence."""
import json
from pathlib import Path
import random
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS))

from _lib.runtime import case_core as core
from _lib.world import single_world


class FailingWriter:
    def __init__(self, stream):
        self.stream = stream

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def write(self, data):
        raise OSError('injected image write failure')

    def __getattr__(self, name):
        return getattr(self.stream, name)


class JpegPackReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = tempfile.TemporaryDirectory()
        cls.paths = []
        for i, (mode, size) in enumerate([
                ('RGB', (960, 540)), ('RGBA', (720, 1280)), ('L', (192, 128))], 1):
            channels = {'RGB': 3, 'RGBA': 4, 'L': 1}[mode]
            data = random.Random(600 + i).randbytes(size[0] * size[1] * channels)
            image = Image.frombytes(mode, size, data)
            path = Path(cls.frozen.name) / f'frame_{i:04d}.png'
            image.save(path)
            image.close()
            cls.paths.append(path)

    @classmethod
    def tearDownClass(cls):
        cls.frozen.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'packed'

    def materialize(self, tiers, **kwargs):
        return core.materialize_pack(self.paths, tiers, self.output, 'global', **kwargs)

    def sentinel(self):
        self.output.mkdir()
        target = self.output / 'global__frame_0001.jpg'
        target.write_bytes(b'previous durable output')
        return target

    def unchanged_sentinel(self, target):
        self.assertEqual(target.read_bytes(), b'previous durable output')
        self.assertEqual(list(self.output.iterdir()), [target])

    def test_selected_bytes_are_reused_and_metadata_stays_serializable(self):
        tiers = [(3, 512, 75)]
        metadata = core.pack(self.paths, tiers)
        expected = {p.stem: core._jpeg(p, 512, 75) for p in self.paths}
        with patch.object(core, '_jpeg', wraps=core._jpeg) as encode:
            result = self.materialize(tiers)
        self.assertEqual(encode.call_count, 3)
        self.assertEqual(set(metadata), {'tier', 'width', 'quality', 'paths', 'estimated_bytes'})
        self.assertEqual({k: v for k, v in result.items() if k != 'frames'},
                         {k: v for k, v in metadata.items() if k != 'paths'})
        json.dumps(metadata)
        json.dumps(result)
        for frame in result['frames']:
            target = Path(frame['path'])
            self.assertEqual(target.read_bytes(), expected[frame['frame_id']])
            self.assertEqual(frame['sha256'], core.sha256(target))

    def test_budget_fallback_reuses_only_the_winning_tier(self):
        first = [(3, 512, 85)]
        budget = core.pack(self.paths, first)['estimated_bytes'] - 1
        with patch.object(core, '_jpeg', wraps=core._jpeg) as encode:
            result = self.materialize(first + [(2, 128, 50)], budget=budget)
        self.assertEqual(result['tier'], 2)
        self.assertEqual(encode.call_count, 5)  # Three rejected, two selected.
        self.assertEqual([f['frame_id'] for f in result['frames']], ['frame_0001', 'frame_0003'])
        for frame, source in zip(result['frames'], [self.paths[0], self.paths[2]]):
            self.assertEqual(Path(frame['path']).read_bytes(), core._jpeg(source, 128, 50))

    def test_protected_frame_skips_a_tier_without_encoding_it(self):
        with patch.object(core, '_jpeg', wraps=core._jpeg) as encode:
            result = self.materialize([(2, 512, 75), (3, 448, 70)],
                                      protected_paths=[self.paths[1]])
        self.assertEqual(result['tier'], 2)
        self.assertEqual(encode.call_count, 3)
        self.assertEqual([f['frame_id'] for f in result['frames']], [p.stem for p in self.paths])

    def test_exact_budget_and_one_byte_below(self):
        text, schema = '冻结图片', {'description': '道路'}
        tiers = [(3, 512, 75)]
        budget = core.pack(self.paths, tiers, text=text, schema=schema)['estimated_bytes']
        result = self.materialize(tiers, text=text, schema=schema, budget=budget)
        self.assertEqual(result['estimated_bytes'], budget)
        below = self.root / 'below'
        with self.assertRaisesRegex(RuntimeError, '^packing_budget_exceeded$'):
            core.materialize_pack(self.paths, tiers, below, 'global',
                                  text=text, schema=schema, budget=budget - 1)
        self.assertFalse(below.exists())

    def test_individual_image_limit_selects_lower_tier(self):
        cap = max(len(core._jpeg(p, 128, 50)) for p in self.paths)
        self.assertGreater(max(len(core._jpeg(p, 512, 85)) for p in self.paths), cap)
        with patch.object(core, '_jpeg', wraps=core._jpeg) as encode:
            result = self.materialize([(3, 512, 85), (3, 128, 50)], max_image_bytes=cap)
        self.assertEqual(result['tier'], 2)
        self.assertEqual(encode.call_count, 6)
        self.assertTrue(all(Path(f['path']).stat().st_size <= cap for f in result['frames']))

    def test_no_tier_fits_without_creating_output(self):
        for kwargs in ({'budget': 0}, {'max_image_bytes': 1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(RuntimeError, '^packing_budget_exceeded$'):
                    self.materialize([(3, 512, 75)], **kwargs)
                self.assertFalse(self.output.exists())

    def test_encoder_error_propagates_before_any_output_is_created(self):
        original = core._jpeg
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('injected JPEG encoding failure')
            return original(*args, **kwargs)
        with patch.object(core, '_jpeg', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'injected JPEG encoding failure'):
                self.materialize([(3, 512, 75)])
        self.assertFalse(self.output.exists())

    def test_atomic_replace_error_keeps_previous_output_and_cleans_temporary(self):
        target = self.sentinel()
        with patch.object(core.os, 'replace', side_effect=OSError('injected replace failure')):
            with self.assertRaisesRegex(OSError, 'injected replace failure'):
                self.materialize([(3, 512, 75)])
        self.unchanged_sentinel(target)

    def test_write_error_keeps_previous_output_and_cleans_temporary(self):
        target = self.sentinel()
        original = core.os.fdopen
        def fail_write(fd, mode, *args, **kwargs):
            stream = original(fd, mode, *args, **kwargs)
            return FailingWriter(stream) if mode == 'wb' else stream
        with patch.object(core.os, 'fdopen', side_effect=fail_write):
            with self.assertRaisesRegex(OSError, 'injected image write failure'):
                self.materialize([(3, 512, 75)])
        self.unchanged_sentinel(target)

    def test_fsync_error_keeps_previous_output_and_cleans_temporary(self):
        target = self.sentinel()
        with patch.object(core.os, 'fsync', side_effect=OSError('injected fsync failure')):
            with self.assertRaisesRegex(OSError, 'injected fsync failure'):
                self.materialize([(3, 512, 75)])
        self.unchanged_sentinel(target)

    def test_frozen_request_binds_identical_images_and_rejects_tampering(self):
        case = self.root / 'case_pack'
        originals = case / 'frames/probe'
        originals.mkdir(parents=True)
        paths = []
        for source in self.paths:
            target = originals / source.name
            shutil.copyfile(source, target)
            paths.append(target)
        packed = core.materialize_pack(paths, [(3, 512, 75)], case / 'frames/packed/global', 'global')
        core.atomic_json(case / 'case.json', {'case_id': case.name, 'parameters': {}})
        core.atomic_json(case / 'state/execution_policy.json',
                         {'semantic_compilation': 'visual-answers.v2', 'actor_fact_review': 'actor-facts.v2'})
        core.atomic_json(case / 'state/global_frames.json', {'frames': [
            dict(**row, source_path=str(source), time_s=index * 1.5)
            for index, (row, source) in enumerate(zip(packed['frames'], paths))]})
        request = single_world.packet(case)
        single_world.validate_packet(case, request)
        for path in [Path(packed['frames'][0]['path']), paths[0]]:
            original = path.read_bytes()
            path.write_bytes(original + b'tamper')
            try:
                with self.assertRaisesRegex(Exception, 'World (image|original image) bytes changed'):
                    single_world.validate_packet(case, request)
            finally:
                path.write_bytes(original)
        single_world.validate_packet(case, request)


if __name__ == '__main__':
    unittest.main()
