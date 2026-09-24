"""GroundingDINO + SAM, Depth Anything V2 and GG-CNN on camera observations."""

from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, ImageDraw
import torch
from scipy.ndimage import gaussian_filter, distance_transform_edt

from .geometry import depth_comparison, grasp_pose


class Perception:
    def __init__(self, options, media):
        self.options, self.media = options, Path(media).resolve()
        self.root = Path(options["models_dir"]).expanduser().resolve()
        self.device = options.get("device", "cuda")
        self.models = {}
        torch.set_num_threads(4)

    def path(self, name):
        path = (self.media / name).resolve()
        if not path.is_relative_to(self.media) or not path.is_file():
            raise ValueError("Unknown observation artifact")
        return path

    def frame(self, frame_ref):
        with np.load(self.path(frame_ref), allow_pickle=False) as data:
            return {key: data[key] for key in data.files}

    def _model(self, name):
        if name not in self.models:
            from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
            from transformers import SamProcessor, SamModel, AutoImageProcessor, AutoModelForDepthEstimation
            classes = {"grounding-dino": (AutoProcessor, AutoModelForZeroShotObjectDetection),
                       "sam": (SamProcessor, SamModel),
                       "depth-anything": (AutoImageProcessor, AutoModelForDepthEstimation)}
            processor, model = classes[name]
            directory = self.root / name
            self.models[name] = (processor.from_pretrained(directory, local_files_only=True),
                model.from_pretrained(directory, local_files_only=True, use_safetensors=True).to(self.device).eval())
        return self.models[name]

    @torch.inference_mode()
    def segment(self, frame_ref, query):
        frame = self.frame(frame_ref)
        image = Image.fromarray(frame["rgb"])
        processor, model = self._model("grounding-dino")
        query = query.lower().strip().rstrip(".") + "."
        inputs = processor(images=image, text=query, return_tensors="pt").to(self.device)
        outputs = model(**inputs)
        result = processor.post_process_grounded_object_detection(outputs, inputs.input_ids,
            threshold=self.options.get("box_threshold", .25), text_threshold=.20,
            target_sizes=[image.size[::-1]])[0]
        boxes = result["boxes"].cpu().numpy()
        scores = result["scores"].cpu().numpy()
        # A truncated target does not provide enough geometry for this top-down gripper.
        keep = [i for i, box in enumerate(boxes) if box[0] > 2 and box[1] > 2
                and box[2] < image.width - 2 and box[3] < image.height - 2
                and 8 < box[2]-box[0] < image.width * .6
                and 8 < box[3]-box[1] < image.height * .6]
        if not keep:
            return {"found": False, "query": query, "reason": "GroundingDINO found no confident target; do not move."}
        # A single-object demonstration must not silently choose among ambiguous targets.
        keep.sort(key=lambda i: float(scores[i]), reverse=True)
        i = keep[0]
        box = boxes[i]
        if len(keep) > 1:
            second = boxes[keep[1]]
            intersection = np.prod(np.maximum(0, np.minimum(box[2:], second[2:]) - np.maximum(box[:2], second[:2])))
            overlap = intersection / max(1., np.prod(box[2:]-box[:2]))
            if overlap < .5 and scores[keep[1]] > scores[i] * .85:
                return {"found": False, "reason": "Multiple plausible targets; request a more specific description."}
        processor, model = self._model("sam")
        inputs = processor(images=image, input_boxes=[[box.tolist()]], return_tensors="pt").to(self.device)
        outputs = model(**inputs)
        masks = processor.image_processor.post_process_masks(outputs.pred_masks.cpu(),
            inputs["original_sizes"].cpu(), inputs["reshaped_input_sizes"].cpu())[0][0]
        best = int(outputs.iou_scores[0, 0].argmax())
        mask = masks[best].numpy().astype(bool)
        if mask.sum() < 100:
            return {"found": False, "reason": "Segmentation is too small or uncertain"}
        token = uuid4().hex
        mask_ref, overlay_ref = f"{token}-mask.npz", f"{token}-segment.png"
        np.savez_compressed(self.media / mask_ref, mask=mask, frame_ref=frame_ref)
        rgb = frame["rgb"].copy()
        rgb[mask] = (rgb[mask] * .5 + np.array([40, 230, 140]) * .5).astype(np.uint8)
        overlay = Image.fromarray(rgb)
        draw = ImageDraw.Draw(overlay)
        draw.rectangle(box.tolist(), outline="yellow", width=2)
        draw.text((10, 10), f"GroundingDINO + SAM | {query} | {scores[i]:.3f}", fill="white", stroke_width=1, stroke_fill="black")
        overlay.save(self.media / overlay_ref)
        return {"found": True, "mask_ref": mask_ref, "box_xyxy": box.tolist(), "score": float(scores[i]),
                "mask_pixels": int(mask.sum()), "frame_ref": frame_ref, "images": {"segmentation": overlay_ref}}

    @torch.inference_mode()
    def depth(self, frame_ref):
        frame = self.frame(frame_ref)
        processor, model = self._model("depth-anything")
        inputs = processor(images=Image.fromarray(frame["rgb"]), return_tensors="pt").to(self.device)
        outputs = model(**inputs)
        predicted = torch.nn.functional.interpolate(outputs.predicted_depth.unsqueeze(1),
            size=frame["sensor_depth"].shape, mode="bicubic", align_corners=False)[0, 0].cpu().numpy()
        measured = frame["sensor_depth"]
        metrics = depth_comparison(predicted, measured)
        mode = self.options.get("depth_mode", "rgbd")
        if mode == "depth_anything":
            used = predicted
            source = "Depth Anything V2 metric indoor prediction"
        elif mode == "rgbd":
            # Predicted depth is assessed explicitly; it is not silently relabelled as a measurement.
            used = measured.copy()
            source = "SAPIEN RGB-D camera measurement; Depth Anything evaluated separately"
        else:
            raise ValueError(f"Unsupported depth mode: {mode}")
        token = uuid4().hex
        ref, image_ref = f"{token}-depth.npz", f"{token}-depth.png"
        np.savez_compressed(self.media / ref, depth=used, predicted=predicted, frame_ref=frame_ref)
        panels = []
        for values, label in [(predicted, "Depth Anything prediction (m)"), (measured, "SAPIEN RGB-D measurement (m)")]:
            clean = np.nan_to_num(values, nan=0.)
            colors = cv2.applyColorMap(np.uint8(np.clip(clean / 1.5, 0, 1) * 255), cv2.COLORMAP_TURBO)[..., ::-1]
            panel = Image.fromarray(colors)
            ImageDraw.Draw(panel).text((10, 10), label, fill="white", stroke_width=1, stroke_fill="black")
            panels.append(np.asarray(panel))
        Image.fromarray(np.concatenate(panels, axis=1)).save(self.media / image_ref)
        return {"depth_ref": ref, "source": source, "unit": "m", "comparison": metrics,
                "images": {"depth_comparison": image_ref}}

    @torch.inference_mode()
    def propose(self, frame_ref, mask_ref, depth_ref):
        frame = self.frame(frame_ref)
        with np.load(self.path(mask_ref), allow_pickle=False) as data:
            if str(data["frame_ref"]) != frame_ref:
                raise ValueError("Mask and observation do not match")
            mask = data["mask"]
        with np.load(self.path(depth_ref), allow_pickle=False) as data:
            if str(data["frame_ref"]) != frame_ref:
                raise ValueError("Depth and observation do not match")
            depth = data["depth"].copy()
            predicted = data["predicted"]
        valid = mask & np.isfinite(depth) & (depth > .05)
        if valid.sum() < 100:
            raise ValueError("Not enough valid target depth pixels")
        if self.options.get("depth_mode", "rgbd") == "depth_anything":
            measured = frame["sensor_depth"]
            comparable = valid & np.isfinite(measured) & (measured > .05)
            if comparable.sum() < 100:
                raise ValueError("Not enough measured target depth to validate a monocular grasp")
            error = float(np.median(np.abs(predicted[comparable] - measured[comparable])))
            if error > .015:
                raise ValueError(f"Predicted target depth error {error:.3f} m exceeds grasp tolerance; do not move")
        rows, cols = np.nonzero(mask)
        size = min(400, max(200, int(max(np.ptp(rows), np.ptp(cols)) * 4)))
        left = int(np.clip((cols.min()+cols.max()) / 2 - size/2, 0, mask.shape[1]-size))
        top = int(np.clip((rows.min()+rows.max()) / 2 - size/2, 0, mask.shape[0]-size))
        crop = depth[top:top+size, left:left+size].copy()
        crop[~np.isfinite(crop)] = np.nanmedian(crop)
        crop = cv2.resize(crop, (300, 300), interpolation=cv2.INTER_LINEAR)
        crop = np.clip(crop-crop.mean(), -1, 1).astype(np.float32)
        if "ggcnn" not in self.models:
            from .ggcnn import GGCNN
            network = GGCNN().to(self.device)
            network.load_state_dict(torch.load(self.root / "ggcnn.pt", map_location=self.device, weights_only=True))
            self.models["ggcnn"] = network.eval()
        maps = self.models["ggcnn"](torch.from_numpy(crop[None, None]).to(self.device))
        quality, cosine, sine, width = [value[0, 0].cpu().numpy() for value in maps]
        quality = gaussian_filter(quality, 2.)
        angles = gaussian_filter(np.arctan2(sine, cosine) / 2, 2.)
        widths = gaussian_filter(width * 150, 1.)
        target_mask = cv2.resize(np.uint8(mask[top:top+size, left:left+size]), (300, 300), interpolation=cv2.INTER_NEAREST).astype(bool)
        distance = distance_transform_edt(target_mask)
        interior = distance > max(2, distance.max() * .45)
        if not interior.any():
            raise ValueError("Target has no confident grasp interior")
        ranking = np.where(interior, quality, -np.inf)
        row, col = np.unravel_index(ranking.argmax(), ranking.shape)
        score = float(quality[row, col])
        if score < self.options.get("grasp_threshold", .10):
            raise ValueError(f"GG-CNN grasp confidence is too low: {score:.3f}")
        u, v = left + col * size / 300, top + row * size / 300
        pixel_depth = float(np.nanmedian(depth[max(0,int(v)-2):int(v)+3, max(0,int(u)-2):int(u)+3]))
        angle, width_px = float(angles[row, col]), float(widths[row, col] * size / 300)
        center, rotation, width_m = grasp_pose([u, v], angle, width_px, pixel_depth,
            frame["intrinsics"], frame["world_from_camera"])
        overlay = Image.fromarray(frame["rgb"])
        draw = ImageDraw.Draw(overlay)
        d = np.array([np.cos(angle), -np.sin(angle)]) * width_px / 2
        a, b = np.array([u, v])-d, np.array([u, v])+d
        draw.line([tuple(a), tuple(b)], fill="cyan", width=3)
        tangent = np.array([np.sin(angle), np.cos(angle)]) * 10
        for point in (a, b):
            draw.line([tuple(point-tangent), tuple(point+tangent)], fill="yellow", width=4)
        draw.ellipse((u-4,v-4,u+4,v+4), fill="red")
        draw.text((10,10), f"GG-CNN | quality={score:.3f} | width={width_m*1000:.1f} mm", fill="white", stroke_width=1, stroke_fill="black")
        ref = f"{uuid4().hex}-grasp.png"
        overlay.save(self.media / ref)
        return {"position_world_m": center.tolist(), "rotation_world": rotation.tolist(),
                "width_m": width_m, "pixel_uv": [u,v], "angle_rad": angle, "score": score,
                "revision": int(frame["revision"]), "depth_mode": self.options.get("depth_mode", "rgbd"),
                "frame_ref": frame_ref, "images": {"grasp_prediction": ref}}
