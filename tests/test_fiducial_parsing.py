"""
Unit test cho các hàm THUẦN trong fiducial_detector/fiducial_service.py.
KHÔNG cần model .pt thật / KHÔNG cần ultralytics cài đặt - chỉ test:
  - chọn candidate tốt nhất (_pick_best_candidate)
  - tinh chỉnh sub-pixel bằng OpenCV (_refine_center_subpixel) trên ảnh tổng hợp
  - decode ảnh base64
  - hành vi "mock mode" khi chưa có model (đúng với tình trạng hiện tại - chưa có .pt)
"""

import base64

import cv2
import numpy as np
import pytest

from fiducial_service import (
    DetectMarkerResponse,
    _pick_best_candidate,
    _refine_center_subpixel,
    _select_class_indices,
    decode_base64_image,
    detect_marker_in_image,
)


def test_pick_best_candidate_highest_confidence():
    candidates = [
        {"bbox": [0, 0, 10, 10], "confidence": 0.4, "class_id": 0},
        {"bbox": [100, 100, 110, 110], "confidence": 0.9, "class_id": 0},
        {"bbox": [50, 50, 60, 60], "confidence": 0.2, "class_id": 0},
    ]
    best = _pick_best_candidate(candidates, image_shape=(1080, 1920))
    assert best["confidence"] == 0.9


def test_pick_best_candidate_tie_break_by_center_distance():
    # 2 candidate confidence gần bằng nhau (chênh <= 0.03) -> chọn candidate gần tâm ảnh (960,540) hơn
    candidates = [
        {"bbox": [900, 500, 1000, 580], "confidence": 0.80, "class_id": 0},  # gần tâm
        {"bbox": [0, 0, 50, 50], "confidence": 0.81, "class_id": 0},         # xa tâm, conf nhỉnh hơn 1 chút
    ]
    best = _pick_best_candidate(candidates, image_shape=(1080, 1920))
    assert best["bbox"] == [900, 500, 1000, 580]


def test_pick_best_candidate_empty_returns_none():
    assert _pick_best_candidate([], image_shape=(1080, 1920)) is None


def test_select_class_indices_no_filter_returns_none():
    assert _select_class_indices({0: "fiducial_circle"}, None) is None


def test_select_class_indices_matches_substring_case_insensitive():
    names = {0: "board_defect", 1: "FiducialCircle"}
    assert _select_class_indices(names, "circle") == [1]


def _make_synthetic_circle_image(cx: float, cy: float, radius: int, size=(200, 200)) -> np.ndarray:
    """Ảnh tổng hợp: nền đen, 1 vòng tròn trắng đặc - mô phỏng marker thật.
    Lưu ý: cv2.circle() chỉ nhận tâm số nguyên, nên tâm THỰC SỰ được vẽ ra là
    (round(cx), round(cy)), không phải giá trị float truyền vào - test phải so sánh
    với tâm đã làm tròn này, không phải giá trị mong muốn ban đầu."""
    img = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    rendered_cx, rendered_cy = int(round(cx)), int(round(cy))
    cv2.circle(img, (rendered_cx, rendered_cy), radius, (255, 255, 255), thickness=-1)
    return img, rendered_cx, rendered_cy


def test_refine_center_subpixel_finds_true_circle_center():
    true_cx, true_cy, radius = 100.4, 95.7, 20
    img, rendered_cx, rendered_cy = _make_synthetic_circle_image(true_cx, true_cy, radius, size=(200, 200))
    # bbox thô kiểu YOLO, hơi lệch tâm 1 chút so với tâm thật (mô phỏng bbox không hoàn hảo)
    bbox = [true_cx - radius - 3, true_cy - radius + 2, true_cx + radius + 2, true_cy + radius - 1]

    cx, cy, refined = _refine_center_subpixel(img, bbox)

    assert refined is True
    # So sánh với tâm THỰC SỰ được raster hoá (số nguyên), cho phép sai số ~1.5px do
    # hiệu ứng làm tròn/threshold trên ảnh rời rạc.
    assert cx == pytest.approx(rendered_cx, abs=1.5)
    assert cy == pytest.approx(rendered_cy, abs=1.5)


def _make_synthetic_square_image(cx: float, cy: float, half: int, size=(200, 200)):
    """Ảnh tổng hợp: nền đen, 1 hình vuông trắng đặc - mô phỏng marker vuông trên board 2 mặt.
    cv2.rectangle nhận toạ độ số nguyên, tâm thực sự = (round(cx), round(cy))."""
    img = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    rendered_cx, rendered_cy = int(round(cx)), int(round(cy))
    cv2.rectangle(
        img,
        (rendered_cx - half, rendered_cy - half),
        (rendered_cx + half, rendered_cy + half),
        (255, 255, 255),
        thickness=-1,
    )
    return img, rendered_cx, rendered_cy


def test_refine_center_subpixel_finds_true_square_center():
    """Bước tinh chỉnh shape-agnostic phải tìm đúng tâm hình VUÔNG (không chỉ hình tròn)."""
    true_cx, true_cy, half = 105.3, 92.6, 18
    img, rendered_cx, rendered_cy = _make_synthetic_square_image(true_cx, true_cy, half, size=(220, 220))
    # bbox thô kiểu YOLO, hơi lệch so với hình vuông thật
    bbox = [true_cx - half - 3, true_cy - half + 2, true_cx + half + 2, true_cy + half - 1]

    cx, cy, refined = _refine_center_subpixel(img, bbox)

    assert refined is True
    assert cx == pytest.approx(rendered_cx, abs=1.5)
    assert cy == pytest.approx(rendered_cy, abs=1.5)


def test_refine_center_subpixel_fallback_on_blank_roi():
    img = np.zeros((200, 200, 3), dtype=np.uint8)  # không có gì để tìm contour
    bbox = [50, 50, 70, 70]
    cx, cy, refined = _refine_center_subpixel(img, bbox)
    assert refined is False
    assert cx == pytest.approx(60.0)
    assert cy == pytest.approx(60.0)


def test_decode_base64_image_roundtrip():
    img, _, _ = _make_synthetic_circle_image(50, 50, 10, size=(100, 100))
    ok, buf = cv2.imencode(".png", img)
    assert ok
    b64 = base64.b64encode(buf.tobytes()).decode("utf-8")

    decoded = decode_base64_image(b64)
    assert decoded is not None
    assert decoded.shape[0] == 100 and decoded.shape[1] == 100


def test_decode_base64_image_invalid_returns_none():
    assert decode_base64_image("khong-phai-base64-hop-le-!!!") is None


def test_detect_marker_in_image_mock_mode_reports_clearly():
    """Chưa có file .pt (đúng tình trạng hiện tại) -> phải trả found=False + message rõ ràng,
    KHÔNG được raise exception hay trả kết quả giả."""
    from fiducial_service import YOLO_AVAILABLE

    img, _, _ = _make_synthetic_circle_image(50, 50, 10, size=(100, 100))
    result = detect_marker_in_image(img, confidence_threshold=0.5)

    assert isinstance(result, DetectMarkerResponse)
    if not YOLO_AVAILABLE:
        assert result.found is False
        assert result.message is not None and "Model chua san sang" in result.message
