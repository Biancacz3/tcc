"""Crop geometry tests; no ONNX model, backend, API or photo fixture."""

import sys
from pathlib import Path
from unittest import TestCase

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from diagnosticar_foto_adesivos import find_plant_crop, make_regions, remap_detections


class DiagnosticGeometryTests(TestCase):
    def test_cropped_boxes_map_to_original_without_changing_local_box(self):
        local = [{"label": "pulgao", "confidence": .42, "xyxy": [10., 20., 30., 40.]}]
        mapped = remap_detections(local, 120, 70)
        self.assertEqual(mapped[0]["xyxy"], [130., 90., 150., 110.])
        self.assertEqual(local[0]["xyxy"], [10., 20., 30., 40.])

    def test_green_plant_crop_excludes_distant_marker_and_tiles_overlap(self):
        rgb = np.full((720, 1280, 3), 105, dtype=np.uint8)
        rgb[95:350, 180:535] = (90, 220, 45)
        rgb[365:620, 195:520] = (220, 220, 185)  # parte clara da acelga
        rgb[200:300, 750:850] = (255, 255, 255)  # papel do marcador
        box, warning = find_plant_crop(rgb)
        self.assertIsNone(warning)
        self.assertLess(box[0], 180)
        self.assertGreater(box[2], 535)
        self.assertLess(box[2], 750)  # etiqueta nao entra no recorte
        self.assertLessEqual(box[1], 95)
        self.assertGreater(box[3], 620)
        regions = make_regions(rgb, box)
        self.assertEqual(regions["planta"]["rgb"].shape[:2], (box[3]-box[1], box[2]-box[0]))
        self.assertGreater(regions["superior"]["box"][3], regions["inferior"]["box"][1])

    def test_no_green_plant_does_not_make_up_a_crop(self):
        rgb = np.full((720, 1280, 3), 100, dtype=np.uint8)
        box, reason = find_plant_crop(rgb)
        self.assertIsNone(box)
        self.assertTrue(reason)
