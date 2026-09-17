"""Grounding DINO + SAM inference; no simulator imports or object metadata."""

from pathlib import Path

import numpy as np


def surface_point(mask, depth, intrinsic, extrinsic, min_pixels=8):
    """Median of measured surface points in world coordinates (CV camera convention)."""
    valid = mask & np.isfinite(depth) & (depth > 0.05) & (depth < 5.)
    y, x = np.nonzero(valid)
    if len(x) < min_pixels:
        return None
    pixels = np.stack((x, y, np.ones_like(x)))
    camera_points = (np.linalg.inv(intrinsic) @ pixels) * depth[y, x]
    world = np.linalg.inv(extrinsic) @ np.vstack((camera_points, np.ones(len(x))))
    return np.median(world[:3], axis=1).tolist()


class GroundedSAM:
    def __init__(self, config):
        import torch
        from transformers import AutoProcessor, GroundingDinoForObjectDetection, SamModel, SamProcessor
        torch.set_num_threads(config.get('cpu_threads', 4))
        self.torch, self.config = torch, config
        self.device = config.get('device', 'cuda')
        self.detector_processor = AutoProcessor.from_pretrained(config['detector'], local_files_only=True)
        self.detector = GroundingDinoForObjectDetection.from_pretrained(
            config['detector'], local_files_only=True).to(self.device).eval()
        self.segmenter_processor = SamProcessor.from_pretrained(config['segmenter'], local_files_only=True)
        self.segmenter = SamModel.from_pretrained(config['segmenter'], local_files_only=True).to(self.device).eval()

    def inspect(self, rgb, output, sensor=None, initial_rgb=None, query=None):
        from PIL import Image, ImageDraw
        from torchvision.ops import nms
        image = Image.open(rgb).convert('RGB')
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        query = self.config['query'] if query is None else query
        inputs = self.detector_processor(images=image, text=query, return_tensors='pt').to(self.device)
        with self.torch.inference_mode():
            prediction = self.detector(**inputs)
        found = self.detector_processor.post_process_grounded_object_detection(
            prediction, inputs.input_ids, threshold=self.config.get('box_threshold', .25),
            text_threshold=self.config.get('text_threshold', .2), target_sizes=[image.size[::-1]])[0]
        keep = nms(found['boxes'], found['scores'], self.config.get('nms_threshold', .5))
        keep = keep[:self.config.get('max_detections', 8)]
        boxes = found['boxes'][keep].cpu().tolist()
        scores = found['scores'][keep].cpu().tolist()
        labels = [found['text_labels'][int(i)] for i in keep]
        detections, masks = [], []
        packet = np.load(sensor) if sensor else None
        if boxes:
            inputs = self.segmenter_processor(images=image, input_boxes=[boxes], return_tensors='pt').to(self.device)
            with self.torch.inference_mode():
                result = self.segmenter(**inputs, multimask_output=False)
            masks = self.segmenter_processor.image_processor.post_process_masks(
                result.pred_masks.cpu(), inputs['original_sizes'].cpu(), inputs['reshaped_input_sizes'].cpu())[0][:, 0].numpy()
            for index, (box, score, label, mask) in enumerate(zip(boxes, scores, labels, masks)):
                point = None if packet is None else surface_point(mask, packet['depth'], packet['intrinsic'], packet['extrinsic'])
                Image.fromarray(mask.astype('uint8') * 255).save(output / f'mask-{index}.png')
                detections.append(dict(id=index, label=label, box=box, score=score,
                    mask_score=float(result.iou_scores[0, index, 0]), surface_world=point,
                    source='grounding_dino+sam+rgbd', mask=f'mask-{index}.png'))
        if packet is not None:
            packet.close()
        overlay = np.asarray(image).copy()
        colors = [(255, 80, 80), (80, 255, 120), (255, 200, 40), (80, 180, 255)]
        for i, mask in enumerate(masks):
            overlay[mask] = (.6 * overlay[mask] + .4 * np.array(colors[i % len(colors)])).astype('uint8')
        overlay = Image.fromarray(overlay)
        draw = ImageDraw.Draw(overlay)
        for d in detections:
            draw.rectangle(d['box'], outline=colors[d['id'] % len(colors)], width=2)
            draw.text((d['box'][0], max(0, d['box'][1]-12)), f"{d['id']} {d['label']} {d['score']:.2f}", fill='white')
        overlay.save(output / 'overlay.png')
        # Visual zoom comes exclusively from detected boxes, never asset coordinates.
        initial = Image.open(initial_rgb).convert('RGB') if initial_rgb else image
        size = self.config.get('detail_crop_size', 160)
        count = max(1, len(detections))
        details = Image.new('RGB', (size*2, (size+22)*count), 'white')
        labels_draw = ImageDraw.Draw(details)
        for i, detection in enumerate(detections):
            box = detection['box']
            labels_draw.text((4, i*(size+22)), f"ID {detection['id']} current | initial", fill='black')
            for j, source in enumerate((image, initial)):
                crop = source.crop(tuple(box)).resize((size, size))
                details.paste(crop, (j*size, i*(size+22)+22))
        details.save(output / 'details.png')
        return {'detections': detections, 'overlay': str(output / 'overlay.png'),
                'details': str(output / 'details.png'), 'query': query, 'models': self.config.get('provenance', {})}
