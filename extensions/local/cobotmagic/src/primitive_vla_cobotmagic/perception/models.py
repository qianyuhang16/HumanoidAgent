"""GroundingDINO, SAM and Depth Anything on real RGB camera observations."""
import json
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, ImageDraw
import torch


class CameraPerception:
    def __init__(self, options, media):
        self.options, self.media = options, Path(media).resolve()
        self.root = Path(options['models_dir']).expanduser().resolve()
        self.device = options.get('device', 'cuda')
        self.models = {}
        torch.set_num_threads(4)

    def path(self, name):
        path = (self.media / name).resolve()
        if not path.is_relative_to(self.media) or not path.is_file():
            raise ValueError('Unknown observation artifact')
        return path

    def frame(self, frame_ref):
        with np.load(self.path(frame_ref), allow_pickle=False) as data:
            return {key: data[key] for key in data.files}

    def _model(self, name):
        if name not in self.models:
            from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
            from transformers import SamProcessor, SamModel, AutoImageProcessor, AutoModelForDepthEstimation
            classes = {'grounding-dino': (AutoProcessor, AutoModelForZeroShotObjectDetection),
                       'sam': (SamProcessor, SamModel),
                       'depth-anything': (AutoImageProcessor, AutoModelForDepthEstimation)}
            processor, model = classes[name]
            directory = self.root / name
            self.models[name] = (processor.from_pretrained(directory, local_files_only=True),
                model.from_pretrained(directory, local_files_only=True, use_safetensors=True).to(self.device).eval())
        return self.models[name]

    @torch.inference_mode()
    def segment(self, frame_ref, query):
        frame = self.frame(frame_ref)
        image = Image.fromarray(frame['rgb'])
        processor, model = self._model('grounding-dino')
        query = query.lower().strip().rstrip('.') + '.'
        inputs = processor(images=image, text=query, return_tensors='pt').to(self.device)
        outputs = model(**inputs)
        result = processor.post_process_grounded_object_detection(outputs, inputs.input_ids,
            threshold=self.options.get('box_threshold', .25), text_threshold=.20,
            target_sizes=[image.size[::-1]])[0]
        boxes = result['boxes'].cpu().numpy()
        scores = result['scores'].cpu().numpy()
        # Keep the existing visibility and ambiguity criteria when selecting a hover target.
        keep = [i for i, box in enumerate(boxes) if box[0] > 2 and box[1] > 2
                and box[2] < image.width - 2 and box[3] < image.height - 2
                and 8 < box[2]-box[0] < image.width * .6
                and 8 < box[3]-box[1] < image.height * .6]
        if not keep:
            return {'found': False, 'query': query, 'reason': 'GroundingDINO found no confident target; do not move.'}
        keep.sort(key=lambda i: float(scores[i]), reverse=True)
        i = keep[0]
        box = boxes[i]
        if len(keep) > 1:
            second = boxes[keep[1]]
            intersection = np.prod(np.maximum(0, np.minimum(box[2:], second[2:]) - np.maximum(box[:2], second[:2])))
            overlap = intersection / max(1., np.prod(box[2:]-box[:2]))
            if overlap < .5 and scores[keep[1]] > scores[i] * .85:
                return {'found': False, 'reason': 'Multiple plausible targets; request a more specific description.'}
        processor, model = self._model('sam')
        inputs = processor(images=image, input_boxes=[[box.tolist()]], return_tensors='pt').to(self.device)
        outputs = model(**inputs)
        masks = processor.image_processor.post_process_masks(outputs.pred_masks.cpu(),
            inputs['original_sizes'].cpu(), inputs['reshaped_input_sizes'].cpu())[0][0]
        best = int(outputs.iou_scores[0, 0].argmax())
        mask = masks[best].numpy().astype(bool)
        if mask.sum() < 100:
            return {'found': False, 'reason': 'Segmentation is too small or uncertain'}
        token = uuid4().hex
        mask_ref, overlay_ref = f'{token}-mask.npz', f'{token}-segment.png'
        np.savez_compressed(self.media / mask_ref, mask=mask, frame_ref=frame_ref)
        rgb = frame['rgb'].copy()
        rgb[mask] = (rgb[mask] * .5 + np.array([40, 230, 140]) * .5).astype(np.uint8)
        overlay = Image.fromarray(rgb)
        draw = ImageDraw.Draw(overlay)
        draw.rectangle(box.tolist(), outline='yellow', width=2)
        draw.text((10, 10), f'GroundingDINO + SAM | {query} | {scores[i]:.3f}', fill='white', stroke_width=1, stroke_fill='black')
        # Center of the visible mask extent, suitable for an above-object hover.
        rows, cols = np.nonzero(mask)
        u, v = float((cols.min()+cols.max())/2), float((rows.min()+rows.max())/2)
        draw.line((u-9,v,u+9,v), fill='red', width=3)
        draw.line((u,v-9,u,v+9), fill='red', width=3)
        overlay.save(self.media / overlay_ref)
        return {'found': True, 'mask_ref': mask_ref, 'box_xyxy': box.tolist(), 'score': float(scores[i]),
                'mask_pixels': int(mask.sum()), 'frame_ref': frame_ref, 'images': {'segmentation': overlay_ref},
                'pixel_uv': [u, v], 'pixel_method': 'center of SAM mask bounds', 'geometry_verified': False}

    @torch.inference_mode()
    def depth(self, frame_ref):
        frame = self.frame(frame_ref)
        rgb = frame['rgb']
        processor, model = self._model('depth-anything')
        inputs = processor(images=Image.fromarray(rgb), return_tensors='pt').to(self.device)
        outputs = model(**inputs)
        predicted = torch.nn.functional.interpolate(outputs.predicted_depth.unsqueeze(1),
            size=rgb.shape[:2], mode='bicubic', align_corners=False)[0,0].cpu().numpy()
        valid = np.isfinite(predicted) & (predicted > 0)
        if not valid.any():
            raise ValueError('Depth Anything returned no valid positive predictions')
        config = json.loads((self.root/'depth-anything/config.json').read_text())
        metric = config.get('depth_estimation_type') == 'metric'
        low, high = np.percentile(predicted[valid], [2,98])
        scaled = np.clip((np.nan_to_num(predicted)-low)/max(float(high-low), 1e-6),0,1)
        colors = cv2.applyColorMap((scaled*255).astype('uint8'), cv2.COLORMAP_TURBO)[...,::-1]
        image = Image.fromarray(colors)
        ImageDraw.Draw(image).text((10,10), 'Depth Anything prediction | NOT measured depth', fill='white', stroke_width=1, stroke_fill='black')
        token = uuid4().hex
        depth_ref, image_ref = f'{token}-depth.npz', f'{token}-depth.png'
        np.savez_compressed(self.media/depth_ref, predicted=predicted, frame_ref=frame_ref)
        image.save(self.media/image_ref)
        return {'depth_ref':depth_ref, 'frame_ref':frame_ref,
                'source':'Depth Anything V2 monocular prediction', 'unit':'m' if metric else 'relative',
                'measured':False, 'metric_accuracy_verified':False, 'used_for_ik':False,
                'percentiles_2_50_98':np.percentile(predicted[valid],[2,50,98]).tolist(),
                'images':{'depth_prediction':image_ref}}
