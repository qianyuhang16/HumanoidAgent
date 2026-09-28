"""Reuse GroundingDINO/SAM loading and inference; adapt depth for RGB-only cameras."""
import json
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, ImageDraw
import torch
from primitive_vla_tabletop.perception import Perception


class CameraPerception(Perception):
    @torch.inference_mode()
    def segment(self, frame_ref, query):
        result = super().segment(frame_ref, query)
        if result['found']:
            with np.load(self.path(result['mask_ref']), allow_pickle=False) as data:
                mask = data['mask']
            rows, cols = np.nonzero(mask)
            # Center of the visible mask extent, suitable for an above-object hover.
            u, v = float((cols.min()+cols.max())/2), float((rows.min()+rows.max())/2)
            result.update(pixel_uv=[u, v], pixel_method='center of SAM mask bounds',
                          geometry_verified=False)
            image = Image.open(self.path(result['images']['segmentation'])).convert('RGB')
            draw = ImageDraw.Draw(image)
            draw.line((u-9,v,u+9,v), fill='red', width=3)
            draw.line((u,v-9,u,v+9), fill='red', width=3)
            image.save(self.path(result['images']['segmentation']))
        return result

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
