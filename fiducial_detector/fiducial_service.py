"""
Fiducial Detector Service - phat hien tam vong tron moc tren board bang YOLO.

Day la SERVICE RIENG BIET, doc lap hoan toan voi AI Detection API phat hien loi board
dang chay production (ai_api_url trong plc_gateway_config.json) - theo dung lua chon da
chot trong docs/Ke_hoach_tu_dong_bu_lech_board.md.

Cach hoat dong:
1. Nhan anh (base64) + confidence_threshold tu gateway (plc_offset_gateway.py).
2. Chay YOLO (ultralytics) de tim bounding box cua marker moc (1 class). Marker co the
   la HINH TRON hoac HINH VUONG - buoc tinh chinh sub-pixel duoi day shape-agnostic.
3. TINH CHINH SUB-PIXEL: vi o ti le FOV hien tai (~0.315mm/px), sai vai pixel = sai
   ~1mm, du de pha ket qua bu lech. Tam bbox YOLO thuong "rung" vai pixel giua cac lan
   suy luan, nen sau khi co bbox tho, crop vung do ra va dung OpenCV (Otsu threshold +
   contour + TRONG TAM moments) de tim tam chinh xac hon - dung cho ca tron va vuong.
4. Tra ve {found, cx, cy, confidence, bbox, refined, num_candidates}.

CHUA CO FILE MODEL (.pt): nguoi dung se cung cap sau. Cho toi luc do, service nay chay
"mock mode" - van khoi dong, van tra loi dung CONTRACT cua API (found=false + message ro
rang), de phan con lai cua he thong (gateway, test) co the duoc viet/kiem thu day du ma
khong can doi co model that. Chi can dat file .pt vao weights/ (xem README.md) va khoi
dong lai service la dung duoc ngay, khong can sua code.

Cau hinh qua file YAML "fiducial_service_config.yaml" canh file nay (tu tao voi gia tri
mac dinh neu chua co):
  weights_path        duong dan model .pt (mac dinh: weights/fiducial_best.pt canh file nay)
  class_name_filter   neu model co nhieu class, loc theo ten chua chuoi nay (khong phan
                       biet hoa/thuong). null = dung moi class, chon confidence cao nhat.
  imgsz                kich thuoc anh dua vao YOLO khi suy luan (mac dinh 960)
  port                 port chay service (mac dinh 8193)
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FiducialDetector")

RUNTIME_DIR = Path(__file__).resolve().parent
CONFIG_FILE = RUNTIME_DIR / "fiducial_service_config.yaml"


class FiducialServiceConfigModel(BaseModel):
    """Persistent service config. Field defaults are the single source of truth."""

    weights_path: str = Field(default_factory=lambda: str(RUNTIME_DIR / "weights" / "fiducial_best.pt"))
    class_name_filter: Optional[str] = None
    imgsz: int = 960
    port: int = 8193
    save_images: bool = True
    saved_images_dir: str = Field(default_factory=lambda: str(RUNTIME_DIR / "LOG_Processed"))


DEFAULT_CONFIG: Dict[str, Any] = FiducialServiceConfigModel().model_dump()


def save_config(config: Dict[str, Any]) -> None:
    """Persist service config to YAML file."""
    CONFIG_FILE.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_config() -> Dict[str, Any]:
    """Load service config from YAML, creating the file with defaults if missing."""
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    try:
        loaded = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError("Config root must be a YAML mapping")

        merged = DEFAULT_CONFIG.copy()
        merged.update({k: v for k, v in loaded.items() if k in merged})

        if merged != loaded:
            save_config(merged)
        return merged
    except Exception as exc:
        logger.warning(f"Khong doc duoc {CONFIG_FILE}, dung gia tri mac dinh: {exc}")
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()


CONFIG: Dict[str, Any] = load_config()

WEIGHTS_PATH = CONFIG["weights_path"]
TARGET_CLASS_NAME = CONFIG["class_name_filter"]
IMGSZ = CONFIG["imgsz"]
SERVICE_PORT = CONFIG["port"]
SAVE_IMAGES = CONFIG["save_images"]
SAVED_IMAGES_DIR = Path(CONFIG["saved_images_dir"])

# ROI mo rong them bao nhieu % quanh bbox YOLO truoc khi tinh chinh sub-pixel
ROI_MARGIN_RATIO = 0.35


# ===============================
# Model loading (mock mode neu chua co file .pt)
# ===============================
YOLO_AVAILABLE = False
_yolo_model = None
_model_class_names: Dict[int, str] = {}

try:
    from ultralytics import YOLO  # type: ignore

    if Path(WEIGHTS_PATH).is_file():
        _yolo_model = YOLO(WEIGHTS_PATH)
        _model_class_names = _yolo_model.names if isinstance(_yolo_model.names, dict) else dict(enumerate(_yolo_model.names))
        YOLO_AVAILABLE = True
        logger.info(f"Da nap YOLO model: {WEIGHTS_PATH} (classes={_model_class_names})")
    else:
        logger.warning(f"Chua tim thay file model tai: {WEIGHTS_PATH}")
        logger.warning("Service chay MOCK MODE - /api/detect-marker se tra found=false.")
        logger.warning(f"Dat file .pt vao dung duong dan tren (hoac sua weights_path trong {CONFIG_FILE}) roi khoi dong lai.")
except ImportError:
    logger.warning("Chua cai ultralytics (pip install ultralytics). Service chay MOCK MODE.")
except Exception as e:
    logger.error(f"Loi nap YOLO model: {e}. Service chay MOCK MODE.")


# ===============================
# Request/Response models
# ===============================
class DetectMarkerRequest(BaseModel):
    image_base64: str
    confidence_threshold: float = 0.5


class DetectMarkerResponse(BaseModel):
    found: bool
    cx: Optional[float] = None
    cy: Optional[float] = None
    confidence: Optional[float] = None
    bbox: Optional[List[float]] = None  # [x1, y1, x2, y2]
    refined: Optional[bool] = None
    num_candidates: int = 0
    message: Optional[str] = None


# ===============================
# Suy luan YOLO + chon candidate tot nhat
# ===============================
def _select_class_indices(class_names: Dict[int, str], target_substring: Optional[str]) -> Optional[List[int]]:
    """Tra list class-index can giu lai, hoac None neu dung het moi class."""
    if not target_substring:
        return None
    target_lower = target_substring.lower()
    matched = [idx for idx, name in class_names.items() if target_lower in str(name).lower()]
    return matched or None  # neu khong khop gi, coi nhu khong loc (an toan hon la tra rong)


def _run_yolo(image_bgr: np.ndarray, confidence_threshold: float) -> List[Dict[str, Any]]:
    """Chay YOLO, tra list candidate: [{bbox:[x1,y1,x2,y2], confidence:float, class_id:int}, ...]"""
    results = _yolo_model.predict(
        source=image_bgr, imgsz=IMGSZ, conf=confidence_threshold, verbose=False,
    )
    allowed_classes = _select_class_indices(_model_class_names, TARGET_CLASS_NAME)

    candidates = []
    for r in results:
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            continue
        for box in boxes:
            cls_id = int(box.cls.item()) if hasattr(box.cls, "item") else int(box.cls)
            if allowed_classes is not None and cls_id not in allowed_classes:
                continue
            conf = float(box.conf.item()) if hasattr(box.conf, "item") else float(box.conf)
            xyxy = box.xyxy[0].tolist() if hasattr(box.xyxy[0], "tolist") else list(box.xyxy[0])
            candidates.append({"bbox": [float(v) for v in xyxy], "confidence": conf, "class_id": cls_id})
    return candidates


def _pick_best_candidate(candidates: List[Dict[str, Any]], image_shape: Tuple[int, int]) -> Optional[Dict[str, Any]]:
    """Chon confidence cao nhat; neu gan bang nhau (trong 0.03) thi uu tien gan tam anh hon."""
    if not candidates:
        return None
    h, w = image_shape[:2]
    cx_img, cy_img = w / 2.0, h / 2.0

    def bbox_center(bbox):
        return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0

    best = max(candidates, key=lambda c: c["confidence"])
    close_ones = [c for c in candidates if best["confidence"] - c["confidence"] <= 0.03]
    if len(close_ones) > 1:
        def dist_to_center(c):
            bx, by = bbox_center(c["bbox"])
            return (bx - cx_img) ** 2 + (by - cy_img) ** 2
        best = min(close_ones, key=dist_to_center)
    return best


# ===============================
# Tinh chinh sub-pixel bang OpenCV (Otsu + contour + trong tam moments)
# ===============================
# SHAPE-AGNOSTIC: dung TRONG TAM (centroid) qua cv2.moments thay vi minEnclosingCircle,
# nen tim tam chinh xac cho CA hinh tron LAN hinh vuong (va cac hinh loi doi xung khac).
# Bo loc dung "extent" (area / dien tich bounding-rect) de loai nhieu, khong thien vi
# hinh tron: hinh tron extent ~ 0.785, hinh vuong thang ~ 1.0, hinh vuong xoay 45 do ~ 0.5.
EXTENT_MIN = 0.40  # duoi nguong nay coi nhu blob rong/nhieu (duong manh, hinh la), bo qua


def _refine_center_subpixel(image_bgr: np.ndarray, bbox: List[float]) -> Tuple[float, float, bool]:
    """
    Tra (cx, cy, refined). refined=True neu tinh chinh bang trong tam contour thanh cong,
    False neu phai fallback ve tam bbox YOLO (contour khong ro rang).

    Dung cho ca hinh tron va hinh vuong (shape-agnostic) - trong tam moments la tam hinh
    hoc dung cho moi hinh loi doi xung.
    """
    h, w = image_bgr.shape[:2]
    x1, y1, x2, y2 = bbox
    bw, bh = x2 - x1, y2 - y1
    mx, my = bw * ROI_MARGIN_RATIO, bh * ROI_MARGIN_RATIO

    rx1 = max(0, int(x1 - mx))
    ry1 = max(0, int(y1 - my))
    rx2 = min(w, int(x2 + mx))
    ry2 = min(h, int(y2 + my))

    bbox_cx, bbox_cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0

    if rx2 <= rx1 or ry2 <= ry1:
        return bbox_cx, bbox_cy, False

    roi = image_bgr[ry1:ry2, rx1:rx2]
    roi_area = (rx2 - rx1) * (ry2 - ry1)
    try:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # ROI gan nhu dong nhat (khong co canh/tuong phan nao dang ke) -> Otsu se chon
        # nguong vo nghia va findContours co the tra ve ca hinh chu nhat ROI. Khong co
        # tinh chinh trong truong hop nay, fallback thang ve tam bbox YOLO.
        if float(gray.std()) < 3.0:
            return bbox_cx, bbox_cy, False

        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        best_center = None
        best_score = -1.0
        # Thu ca 2 chieu threshold (marker co the sang-tren-nen-toi hoac nguoc lai)
        for thresh_type in (cv2.THRESH_BINARY + cv2.THRESH_OTSU, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU):
            _, binary = cv2.threshold(gray, 0, 255, thresh_type)
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            largest = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(largest)
            if area < 4:  # qua nho, coi nhu nhieu
                continue
            # Loai bo contour kieu "ca hinh chu nhat ROI" (artifact cua threshold tren anh
            # gan nhu dong nhat/nhieu) - 1 marker that, voi margin 35% quanh bbox, khong
            # the chiem gan het dien tich ROI.
            if area > 0.85 * roi_area:
                continue
            # Bo loc SHAPE-AGNOSTIC: extent = area / dien tich hinh chu nhat bao ngoai.
            # Loai blob qua rong/manh (nhieu), giu ca hinh tron va hinh vuong.
            bx_, by_, bw_, bh_ = cv2.boundingRect(largest)
            rect_area = float(bw_ * bh_)
            extent = area / rect_area if rect_area > 0 else 0.0
            if extent < EXTENT_MIN:
                continue
            # Trong tam (centroid) qua moments - tam hinh hoc chinh xac cho hinh loi doi xung
            M = cv2.moments(largest)
            if M["m00"] <= 0:
                continue
            ccx = M["m10"] / M["m00"]
            ccy = M["m01"] / M["m00"]
            score = area * extent
            if score > best_score:
                best_score = score
                best_center = (ccx, ccy)

        if best_center is None:
            return bbox_cx, bbox_cy, False

        cx = rx1 + best_center[0]
        cy = ry1 + best_center[1]
        return float(cx), float(cy), True
    except Exception as e:
        logger.warning(f"Tinh chinh sub-pixel loi, fallback ve tam bbox: {e}")
        return bbox_cx, bbox_cy, False


def decode_base64_image(image_base64: str) -> Optional[np.ndarray]:
    try:
        image_bytes = base64.b64decode(image_base64)
        frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        return frame
    except Exception as e:
        logger.error(f"Khong decode duoc anh base64: {e}")
        return None


def _draw_detection_overlay(image_bgr: np.ndarray, result: "DetectMarkerResponse") -> np.ndarray:
    """Ve tam camera (anh), bbox + tam marker phat hien duoc len anh, de xem lai de doi chieu."""
    vis = image_bgr.copy()
    h, w = vis.shape[:2]
    img_cx, img_cy = w / 2.0, h / 2.0
    img_cx_i, img_cy_i = int(round(img_cx)), int(round(img_cy))

    # Tam camera/anh: chu thap mau xanh duong
    cv2.drawMarker(vis, (img_cx_i, img_cy_i), (255, 0, 0), markerType=cv2.MARKER_CROSS, markerSize=24, thickness=2)
    cv2.putText(vis, f"cam=({img_cx:.0f},{img_cy:.0f})", (img_cx_i + 10, img_cy_i - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1, cv2.LINE_AA)

    if result.found and result.bbox is not None:
        x1, y1, x2, y2 = [int(round(v)) for v in result.bbox]
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)

        if result.cx is not None and result.cy is not None:
            cx, cy = int(round(result.cx)), int(round(result.cy))
            cv2.drawMarker(vis, (cx, cy), (0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=20, thickness=2)
            cv2.line(vis, (img_cx_i, img_cy_i), (cx, cy), (0, 255, 255), 1)

            label = f"marker=({result.cx:.1f},{result.cy:.1f}) conf={result.confidence:.2f}"
            cv2.putText(vis, label, (x1, max(0, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 0, 255), 1, cv2.LINE_AA)

            dx, dy = result.cx - img_cx, result.cy - img_cy
            cv2.putText(vis, f"d=({dx:+.1f},{dy:+.1f})px", (cx + 10, cy + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    else:
        cv2.putText(vis, "NOT FOUND", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)

    return vis


def save_processed_image(image_bgr: np.ndarray) -> Optional[str]:
    """Luu anh service nhan duoc theo saved_images/YYYY/MM/DD."""
    if not SAVE_IMAGES:
        return None

    try:
        now = datetime.now()
        day_dir = SAVED_IMAGES_DIR / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
        day_dir.mkdir(parents=True, exist_ok=True)

        file_path = day_dir / f"fiducial_{now.strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        if not cv2.imwrite(str(file_path), image_bgr):
            logger.warning(f"Khong luu duoc anh fiducial: {file_path}")
            return None

        logger.info(f"Da luu anh fiducial: {file_path}")
        return str(file_path)
    except Exception as e:
        logger.warning(f"Luu anh fiducial bi loi, bo qua: {e}")
        return None


def detect_marker_in_image(image_bgr: np.ndarray, confidence_threshold: float) -> DetectMarkerResponse:
    """Ham THUAN (khong phu thuoc FastAPI) - de unit test doc lap, khong can chay HTTP."""
    if not YOLO_AVAILABLE:
        return DetectMarkerResponse(
            found=False, num_candidates=0,
            message=f"Model chua san sang (mock mode). Dat file .pt vao: {WEIGHTS_PATH}",
        )

    candidates = _run_yolo(image_bgr, confidence_threshold)
    if not candidates:
        return DetectMarkerResponse(found=False, num_candidates=0, message="Khong phat hien duoc diem moc nao vuot nguong confidence")

    best = _pick_best_candidate(candidates, image_bgr.shape)
    cx, cy, refined = _refine_center_subpixel(image_bgr, best["bbox"])

    return DetectMarkerResponse(
        found=True, cx=cx, cy=cy, confidence=best["confidence"], bbox=best["bbox"],
        refined=refined, num_candidates=len(candidates),
        message="OK" if refined else "OK (fallback: dung tam bbox YOLO, tinh chinh contour khong ro rang)",
    )


# ===============================
# FastAPI app
# ===============================
app = FastAPI(title="Fiducial Detector Service", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "service": "Fiducial Detector Service",
        "status": "running",
        "yolo_available": YOLO_AVAILABLE,
        "weights_path": WEIGHTS_PATH,
        "class_names": _model_class_names,
        "target_class_filter": TARGET_CLASS_NAME,
        "imgsz": IMGSZ,
        "save_images": SAVE_IMAGES,
        "saved_images_dir": str(SAVED_IMAGES_DIR),
        "config_path": str(CONFIG_FILE),
    }


@app.post("/api/detect-marker", response_model=DetectMarkerResponse)
async def detect_marker(request: DetectMarkerRequest):
    frame = decode_base64_image(request.image_base64)
    if frame is None:
        return DetectMarkerResponse(found=False, num_candidates=0, message="Anh gui len khong hop le / khong decode duoc")
    result = detect_marker_in_image(frame, request.confidence_threshold)
    save_processed_image(_draw_detection_overlay(frame, result))
    return result


if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("Starting Fiducial Detector Service")
    print(f"Weights: {WEIGHTS_PATH} (available={YOLO_AVAILABLE})")
    print(f"URL: http://localhost:{SERVICE_PORT}")
    print(f"Docs: http://localhost:{SERVICE_PORT}/docs")
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")
