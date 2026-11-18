"""Regressoes de significado: candidatos desconhecidos nunca viram positivos."""
from dataclasses import replace
import numpy as np
import pytest
from app import inference
from app.inference import Detector, accepted_box, merge_detections
from app.plant_regions import RegionConfig, locate_regions, _choose_component


def candidate():
    return {"id": 1, "xyxy": [220, 220, 260, 260], "status": "unidentified"}


def localized(items=None, **changes):
    return {"candidates": items if items is not None else [candidate()], "mask": np.ones((480, 480), np.uint8) * 255,
            "plant_bbox": [30, 30, 450, 450], "warnings": [], "usable": True, "budget_exceeded": False, **changes}


def detector(monkeypatch, regions=None, predictions=None, config=None):
    instance = Detector.__new__(Detector)
    instance.config = config or RegionConfig()
    monkeypatch.setattr(inference, "locate_regions", lambda *a, **k: localized() if regions is None else regions)
    instance.calls = 0
    def predict(rgb):
        instance.calls += 1
        size = rgb.shape[0]
        return predictions if predictions is not None else [{"label": "pulgao", "confidence": .8, "xyxy": [size / 2 - 20, size / 2 - 20, size / 2 + 20, size / 2 + 20]}]
    instance._predict_crop = predict
    return instance


def test_candidate_positive_and_context_dedup(monkeypatch):
    det = detector(monkeypatch)
    result = det.analyze(np.zeros((480, 480, 3), np.uint8))
    assert result["status"] == "positive" and len(result["detections"]) == 1
    assert result["detections"][0]["xyxy"] == [220, 220, 260, 260]
    assert result["candidates"][0]["status"] == "detected"
    assert result["unidentified_count"] == 0 and det.calls == 2


def test_unknown_candidate_is_review_not_a_detection(monkeypatch):
    det = detector(monkeypatch, predictions=[])
    result = det.analyze(np.zeros((480, 480, 3), np.uint8))
    assert result["status"] == "review" and result["detections"] == []
    assert result["unidentified_count"] == 1
    assert result["candidates"][0]["status"] == "unidentified"


def test_no_mask_is_inconclusive_not_clean(monkeypatch):
    det = detector(monkeypatch, regions=localized([], mask=None, usable=False, plant_bbox=None))
    result = det.analyze(np.zeros((480, 480, 3), np.uint8))
    assert result["status"] == "inconclusive" and det.calls == 0


def test_valid_plant_without_candidates_has_no_detections(monkeypatch):
    det = detector(monkeypatch, regions=localized([]))
    assert det.analyze(np.zeros((480, 480, 3), np.uint8))["status"] == "no_detections"
    assert det.calls == 0


def test_candidate_budget_does_not_publish_partial_positive(monkeypatch):
    det = detector(monkeypatch, regions=localized([candidate()] * 65))
    result = det.analyze(np.zeros((480, 480, 3), np.uint8))
    assert result["status"] == "inconclusive" and result["detections"] == [] and det.calls == 0


def test_time_budget_stops_before_network_model(monkeypatch):
    det = detector(monkeypatch, config=replace(RegionConfig(), max_processing_seconds=.1))
    clock = iter([0., 1.])
    monkeypatch.setattr(inference.time, "monotonic", lambda: next(clock))
    result = det.analyze(np.zeros((480, 480, 3), np.uint8))
    assert result["status"] == "inconclusive" and not result["detections"] and det.calls == 0


@pytest.mark.parametrize("box", [[0, 0, 480, 480], [0, 0, 300, 300], [-1, 220, 260, 260], [220, 220, 481, 260], [260, 220, 220, 260], [220, 220, float('nan'), 260], [220, 220, float('inf'), 260]])
def test_giant_outside_or_nonfinite_boxes_rejected(box):
    assert not accepted_box({"label": "pulgao", "confidence": .9, "xyxy": box}, candidate(), np.ones((480, 480), np.uint8), RegionConfig())


def test_boxes_on_background_rejected():
    assert not accepted_box({"label": "pulgao", "confidence": .9, "xyxy": [220, 220, 260, 260]}, candidate(), np.zeros((480, 480), np.uint8), RegionConfig())


def test_whole_plant_box_rejected_even_if_candidate_matches():
    box = [20, 20, 450, 450]
    assert not accepted_box({"label": "pulgao", "confidence": .9, "xyxy": box}, {"xyxy": box}, np.ones((480, 480), np.uint8), RegionConfig())


def test_overlap_across_candidates_deduplicated():
    items = [{"label": "pulgao", "confidence": .9, "xyxy": [100, 100, 140, 140]},
             {"label": "pulgao", "confidence": .8, "xyxy": [102, 102, 142, 142]}]
    assert merge_detections(items, .5) == [items[0]]


def test_low_nonfinite_wrong_class_predictions_rejected():
    for score in [.34, 1.1, float('nan')]:
        assert not accepted_box({"label": "pulgao", "confidence": score, "xyxy": [220, 220, 260, 260]}, candidate(), np.ones((480, 480), np.uint8), RegionConfig())
    assert not accepted_box({"label": "person", "confidence": .9, "xyxy": [220, 220, 260, 260]}, candidate(), np.ones((480, 480), np.uint8), RegionConfig())


@pytest.mark.parametrize("color", [(0, 0, 0), (255, 255, 255), (30, 160, 45)])
def test_uniform_images_not_reported_as_free_of_insects(color):
    rgb = np.full((360, 640, 3), color, np.uint8)
    result = locate_regions(rgb)
    assert not result["usable"] and result["mask"] is None


def test_invalid_image_and_config_rejected():
    with pytest.raises(ValueError): locate_regions(np.zeros((200, 200, 3), np.float32))
    with pytest.raises(ValueError): RegionConfig(candidate_match_iou=float('nan'))
    with pytest.raises(ValueError): RegionConfig(max_candidates=9999)
    with pytest.raises(ValueError): RegionConfig(max_candidates=3.5)


def test_tiny_image_is_inconclusive():
    result = locate_regions(np.zeros((50, 100, 3), np.uint8))
    assert not result["usable"] and result["mask"] is None and result["warnings"]


def test_time_budget_after_last_crop_does_not_publish_result(monkeypatch):
    det = detector(monkeypatch, config=replace(RegionConfig(), max_processing_seconds=1.))
    clock = iter([0., .1, .2, 2.])
    monkeypatch.setattr(inference.time, "monotonic", lambda: next(clock))
    result = det.analyze(np.zeros((480, 480, 3), np.uint8))
    assert result["status"] == "inconclusive" and not result["detections"] and det.calls == 2


def test_ambiguous_plants_without_marker():
    stats = np.array([[0, 0, 640, 360, 100000], [20, 20, 100, 180, 10000], [300, 20, 100, 180, 8000]])
    index, reason = _choose_component(stats, None, RegionConfig(), 640 * 360)
    assert index is None and 'mais de uma' in reason


def test_marker_can_select_nearest_component():
    stats = np.array([[0, 0, 640, 360, 100000], [20, 20, 100, 180, 10000], [300, 20, 100, 180, 8000]])
    index, reason = _choose_component(stats, (130, 100), RegionConfig(), 640 * 360)
    assert index == 1 and reason is None
