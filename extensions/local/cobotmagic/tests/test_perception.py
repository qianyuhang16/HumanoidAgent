"""CPU contracts for the camera models; fake network outputs, no weights or ROS."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

try:
    import torch
except ImportError:
    torch = None

if torch is not None:
    from primitive_vla_cobotmagic.perception.models import CameraPerception


class Batch(dict):
    def to(self, device):
        return self

    def __getattr__(self, name):
        return self[name]


@unittest.skipIf(torch is None, 'Run with the configured perception Python environment')
class CameraPerceptionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.media = self.root / 'media'
        self.media.mkdir()
        self.engine = CameraPerception({'models_dir': str(self.root / 'weights'), 'device': 'cpu'}, self.media)
        self.rgb = np.zeros((120, 160, 3), dtype=np.uint8)
        self.frame_ref = 'camera.npz'
        np.savez_compressed(self.media / self.frame_ref, rgb=self.rgb)

    def detection(self, boxes, scores):
        processor = Mock(return_value=Batch(input_ids=torch.tensor([[1]])))
        processor.post_process_grounded_object_detection.return_value = [{
            'boxes': torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4),
            'scores': torch.tensor(scores, dtype=torch.float32),
        }]
        self.engine.models['grounding-dino'] = processor, Mock()
        return processor

    def test_segment_returns_mask_center_and_artifacts_from_rgb_only(self):
        processor = self.detection([[20, 24, 75, 88]], [.9])
        masks = torch.zeros((1, 2, 120, 160), dtype=torch.bool)
        masks[0, 1, 30:70, 25:65] = True
        sam = Mock(return_value=Batch(original_sizes=torch.tensor([[120, 160]]),
                                     reshaped_input_sizes=torch.tensor([[120, 160]])))
        sam.image_processor.post_process_masks.return_value = [masks]
        network = Mock(return_value=SimpleNamespace(pred_masks=masks,
                                                  iou_scores=torch.tensor([[[.1, .9]]])))
        self.engine.models['sam'] = sam, network

        result = self.engine.segment(self.frame_ref, ' Pink Bowl ')

        self.assertTrue(result['found'])
        self.assertEqual(result['pixel_uv'], [44.5, 49.5])
        self.assertEqual(result['mask_pixels'], 1600)
        self.assertEqual(result['frame_ref'], self.frame_ref)
        self.assertFalse(result['geometry_verified'])
        self.assertEqual(processor.call_args.kwargs['text'], 'pink bowl.')
        with np.load(self.media / result['mask_ref'], allow_pickle=False) as artifact:
            np.testing.assert_array_equal(artifact['mask'], masks[0, 1].numpy())
            self.assertEqual(str(artifact['frame_ref']), self.frame_ref)
        with Image.open(self.media / result['images']['segmentation']) as image:
            self.assertEqual(image.size, (160, 120))

    def test_no_target_or_ambiguous_objects_do_not_select_a_pixel(self):
        for boxes, scores, reason in [([], [], 'no confident target'),
                ([[20, 24, 75, 88], [90, 25, 135, 85]], [.9, .89], 'Multiple plausible targets')]:
            with self.subTest(reason=reason):
                self.detection(boxes, scores)
                result = self.engine.segment(self.frame_ref, 'bowl')
                self.assertFalse(result['found'])
                self.assertIn(reason, result['reason'])
                self.assertNotIn('pixel_uv', result)
        self.assertEqual(list(self.media.iterdir()), [self.media / self.frame_ref])

    def test_depth_preserves_prediction_units_without_sensor_depth(self):
        folder = self.engine.root / 'depth-anything'
        folder.mkdir(parents=True)
        self.engine.models['depth-anything'] = (Mock(return_value=Batch()),
            Mock(return_value=SimpleNamespace(predicted_depth=torch.full((1, 2, 3), 2.5))))
        for mode, unit in [('metric', 'm'), ('relative', 'relative')]:
            with self.subTest(mode=mode):
                (folder / 'config.json').write_text(json.dumps({'depth_estimation_type': mode}))
                result = self.engine.depth(self.frame_ref)
                self.assertEqual(result['unit'], unit)
                self.assertEqual(result['frame_ref'], self.frame_ref)
                for name in ('measured', 'metric_accuracy_verified', 'used_for_ik'):
                    self.assertFalse(result[name])
                with np.load(self.media / result['depth_ref'], allow_pickle=False) as artifact:
                    np.testing.assert_allclose(artifact['predicted'], np.full((120, 160), 2.5), atol=1e-5)
                self.assertTrue((self.media / result['images']['depth_prediction']).is_file())

    def test_invalid_depth_does_not_produce_artifacts(self):
        self.engine.models['depth-anything'] = (Mock(return_value=Batch()),
            Mock(return_value=SimpleNamespace(predicted_depth=torch.zeros((1, 2, 3)))))
        with self.assertRaisesRegex(ValueError, 'no valid positive predictions'):
            self.engine.depth(self.frame_ref)
        self.assertEqual(list(self.media.iterdir()), [self.media / self.frame_ref])


if __name__ == '__main__':
    unittest.main()
