# Kế hoạch: Tự động hoá bù lệch board bằng YOLO (thay thế đo thủ công)

## 0. Bối cảnh hiện tại (đã khảo sát code)

Hệ thống hiện có 3 mảnh ghép liên quan, tất cả sẽ được **tái sử dụng**, không viết lại từ đầu:

| File | Vai trò hiện tại |
|---|---|
| `Calib_Phan_Cung_VRS/Calib_4diem/calculate_4diem.py` | Calib **gốc, 1 lần duy nhất** lúc lắp máy: fit ma trận bilinear Board(x,y)→PLC(x,y) từ Excel (`calib_6diem.xlsx`) → lưu `vrs_calib_4diem.json` (c0..c3, d0..d3). |
| `Calib_Phan_Cung_VRS/Calib_4diem/bu_lech_board.py` | Bù lệch **mỗi lần gá board mới**: chọn 2 hoặc 3 điểm mốc (`ANCHOR_POINTS_POOL`: A=(7.682,14.226), C=(345.84,567.78), D=(356.386,7.135)...), tính PLC kỳ vọng bằng `board_to_plc()`, sau đó **operator tự jog máy bằng mắt** tới từng điểm mốc và **gõ tay** toạ độ PLC đọc được, rồi chạy `kabsch_2d()` (Kabsch/Procrustes 2D) ra góc xoay + tịnh tiến (R, t), lưu `offset_runtime.json`. `apply_rigid_offset()` áp (R,t) lên mọi điểm gia công sau đó. |
| `plc_gateway_api_optimized_v2.py` | FastAPI (port 8083): `PLCService` (ghi/đọc PLC Omron), `CameraService` (chụp snapshot Sony streamer), `wait_for_plc_position()` (chờ PLC hết chuyển động qua motion-status D466-D469), `/api/plc/move`, `/api/inspect-defect` (di chuyển + chụp + gọi AI detect lỗi). |
| `Do_khoang_cach_kem_camera_v5.py` | GUI Qt gộp 3 tab, trong đó tab "Bu lech Board (Calib)" chính là bản Qt của `bu_lech_board.py` — vẫn là 2 ô nhập tay PLC X/Y mỗi điểm mốc. |
| `Do_Khoang_Cach/Do_FOV_Calibration.py` | Công cụ đo FOV bằng giấy ô li — chính là nguồn gốc của số 605.15 x 341.5 mm bạn cho. Quy ước: tâm ảnh = (W/2, H/2), lệch pixel = click − tâm. |

**Việc cần tự động hoá = thay bước "operator jog bằng mắt + gõ tay PLC X/Y"** trong `bu_lech_board.py` bằng: PLC tự di chuyển đến điểm kỳ vọng → chụp ảnh → YOLO tìm tâm vòng tròn mốc → suy ra toạ độ PLC "đo được" **không cần jog**. Phần Kabsch, lưu JSON, áp dụng offset — **giữ nguyên 100%**, không đổi để không phá vỡ phần còn lại của hệ thống.

Theo các lựa chọn bạn đã chốt:
- Logic mới → thêm endpoint vào **`plc_gateway_api_optimized_v2.py`** (không tách service riêng).
- YOLO phát hiện vòng tròn mốc → **service/model riêng biệt**, độc lập với AI Detection API lỗi board.
- Chế độ mặc định → **2 điểm mốc (A, C)**, vẫn giữ khả năng chạy 3 điểm khi cần (tham số `anchor_mode`).

---

## 1. Vấn đề toán học cốt lõi cần giải quyết trước tiên

Muốn suy ra "PLC đo được" từ ảnh, không thể chỉ dùng FOV (mm/px) đơn thuần, vì còn 2 ẩn số nữa:

1. **Chiều trục ảnh có thể không khớp chiều trục PLC** (ảnh +X có thể ứng với PLC −X, hoặc camera gắn xoay 90° nên trục ảnh X lại tương ứng trục PLC Y).
2. **Có thể lệch một góc nhỏ** giữa trục quang học của camera và trục cơ khí X/Y của máy (dù lắp thẳng hàng vẫn hiếm khi tuyệt đối 0°).

Grep toàn bộ `D:\Camera\Dev` không thấy chỗ nào từng tính ma trận này — vì trước giờ operator jog bằng mắt nên không cần. Đây là mảnh ghép **bắt buộc phải thêm**, gọi là **ma trận trục camera↔máy `T`**.

### Định nghĩa T
Nếu ra lệnh PLC dịch chuyển `(ΔX, ΔY)` mm, một điểm cố định trên board sẽ dịch trong ảnh một lượng pixel `(Δpx, Δpy)`. Quan hệ này gần như tuyến tính trong vùng làm việc nhỏ:

```
[Δpx]       [ΔX]
[Δpy]  = T·[ΔY]
```

`T` (ma trận 2x2) gộp luôn cả tỉ lệ mm/px, việc đảo trục, và góc xoay lắp đặt — không cần tách riêng từng thành phần.

### Cách hiệu chỉnh T (làm 1 lần duy nhất, không phải mỗi board)
1. Di chuyển PLC tới 1 điểm bất kỳ P0 (dùng luôn điểm mốc A), chụp ảnh, detect tâm marker → pixel `p0`.
2. Di chuyển PLC tới P0 + (Δ, 0) với Δ nhỏ (ví dụ 5mm, đủ để marker vẫn nằm trong khung hình), chụp, detect → `p1`.
3. Di chuyển PLC tới P0 + (0, Δ), chụp, detect → `p2`.
4. `T = [[(p1x−p0x)/Δ, (p2x−p0x)/Δ], [(p1y−p0y)/Δ, (p2y−p0y)/Δ]]`.
5. (Khuyến nghị) lặp lại với vài giá trị Δ khác nhau (±5mm, ±10mm) rồi lấy trung bình/least-squares để chống nhiễu phát hiện — tái dùng chính pipeline PLC-move + capture + YOLO đang xây ở bước 2 dưới, nên gần như miễn phí.

Khi dùng: từ độ lệch pixel `(dx_px, dy_px)` đo được tại 1 điểm mốc, suy ngược ra độ lệch PLC thật:

```
[dx_mm]         [dx_px]
[dy_mm]  = T⁻¹·[dy_px]
```

`T` lưu trong config (`camera_axis_matrix`), chỉ cần hiệu chỉnh lại khi tháo/lắp lại camera hoặc đổi lens/khoảng cách làm việc — **không phải hiệu chỉnh lại mỗi khi đổi board**.

### FOV (605.15 x 341.5mm / 1920x1080) dùng để làm gì?
Dùng làm **giá trị khởi tạo gần đúng** cho `T` trước khi có số đo thực (T ban đầu ≈ ma trận đường chéo `diag(1920/605.15, 1080/341.5)` nếu giả định trục thẳng hàng, không xoay/lật) và dùng để **sanity-check**: sau khi hiệu chỉnh T thực tế, |T| tính ra phải xấp xỉ giá trị này, nếu lệch nhiều (ví dụ ngược dấu, hoặc số quá khác) thì báo nghi ngờ lắp camera sai hướng.

---

## 2. Kiến trúc tổng thể

```
┌────────────────────────┐     ①move+wait     ┌──────────────┐
│                        │ ──────────────────► │   PLC Omron   │
│  plc_gateway_api        │                     └──────────────┘
│  _optimized_v2.py       │     ②snapshot       ┌──────────────┐
│  (port 8083)            │ ──────────────────► │ Sony streamer │
│                        │ ◄────────────────── └──────────────┘
│  + endpoint MỚI:        │
│  /api/calib/            │     ③detect marker  ┌──────────────────────┐
│    auto-board-offset    │ ──────────────────► │ Fiducial Detector API │
│                        │ ◄────────────────── │  (YOLO riêng, port    │
│  + (tuỳ chọn) endpoint  │   {cx, cy, conf}     │  8091, model riêng)   │
│    /api/calib/          │                     └──────────────────────┘
│    camera-axis          │
│                        │
│  ④ dx_px,dy_px → T⁻¹    │
│  ⑤ kabsch_2d() (import  │
│    từ bu_lech_board.py) │
│  ⑥ ghi offset_runtime   │
│    .json (schema CŨ)    │
└────────────────────────┘
```

`bu_lech_board.py`, `apply_rigid_offset()`, `board_to_plc()`, `offset_runtime.json`, `Calib_4diem.py` **không đổi** — endpoint mới chỉ thay nguồn gốc của "measured_points" từ bàn phím → từ ảnh.

---

## 3. Thành phần A — Fiducial Detector Service (YOLO riêng)

### 3.1 Vị trí đề xuất
`D:\Camera\Dev\Calib_Phan_Cung_VRS\Fiducial_Detector\` (thư mục mới, ngang hàng `Calib_4diem`):
```
Fiducial_Detector/
  dataset/
    images/train, images/val
    labels/train, labels/val
    data.yaml
  train_yolo.py
  weights/best.pt
  fiducial_service.py     # FastAPI riêng, port 8091
  requirements.txt
```

### 3.2 Thu thập & gán nhãn dữ liệu
- Chụp 150–300 ảnh thực tế của vòng tròn mốc, ở **đúng camera/ánh sáng/khoảng cách làm việc thật** (không dùng ảnh mạng), với nhiều board khác nhau và nhiều vị trí lệch giả lập (dịch board vài mm theo mọi hướng để mốc xuất hiện ở nhiều chỗ khác nhau trong khung hình, không chỉ ở tâm).
- 1 class duy nhất: `fiducial_circle`.
- Gán nhãn bounding box quanh vòng tròn (dùng LabelImg / Roboflow / CVAT — format YOLO txt: `class cx cy w h` chuẩn hoá 0-1).
- Augmentation: xoay nhẹ, thay đổi sáng/tương phản (mô phỏng điều kiện đèn LED khác nhau), KHÔNG lật ảnh (marker không đối xứng theo hướng lắp, lật sẽ sai ngữ nghĩa nếu marker có chi tiết định hướng).
- Chia 80/20 train/val.

### 3.3 Model & train
- `yolov8n.pt` (nano) làm base — bài toán 1 class, vật thể đơn giản, không cần model lớn; ưu tiên tốc độ vì có thể phải gọi lặp lại 2-3 lần/board.
- `ultralytics` CLI:
```bash
yolo detect train data=dataset/data.yaml model=yolov8n.pt epochs=100 imgsz=960 patience=20
```
- Đánh giá bằng mAP + **quan trọng hơn mAP: sai số tâm tính bằng mm** (xem mục Kiểm thử, phần 6).

### 3.4 `fiducial_service.py` — API riêng
```python
POST /api/detect-marker
Request:  { "image_base64": "...", "confidence_threshold": 0.5 }
Response: {
  "found": true,
  "cx": 963.4, "cy": 538.1,      # tâm mốc đã tinh chỉnh sub-pixel, đơn vị px, gốc top-left
  "confidence": 0.91,
  "bbox": [940, 515, 986, 561],
  "num_candidates": 1
}
```

### 3.5 Tinh chỉnh sub-pixel (BẮT BUỘC, không dùng thẳng tâm bbox YOLO)
Ở scale hiện tại (~0.315 mm/px), sai số vài pixel = sai số ~1mm, đủ để phá kết quả bù lệch. Tâm bbox YOLO thường "rung" vài pixel giữa các lần suy luận. Quy trình tinh chỉnh trong `fiducial_service.py` sau khi có bbox thô:
1. Crop ROI quanh bbox (mở rộng thêm 20-30% biên).
2. Chuyển grayscale, threshold (Otsu) hoặc `cv2.HoughCircles`.
3. Lấy contour lớn nhất → `cv2.minEnclosingCircle()` hoặc mô-men ảnh (`cv2.moments`) → tâm sub-pixel.
4. Cộng lại offset của crop để ra toạ độ tâm trên ảnh gốc.
5. Nếu bước classical CV thất bại (contour rỗng/nhiễu) → fallback dùng tâm bbox YOLO, đánh dấu `"refined": false` trong response để bên gọi biết độ tin cậy thấp hơn.

### 3.6 Xử lý nhiều/không có phát hiện
- Không có detection nào vượt `confidence_threshold` → `found: false`.
- Nhiều detection → chọn theo confidence cao nhất; nếu 2 candidate gần bằng confidence, chọn candidate gần tâm ảnh hơn (vì sau khi PLC move tới điểm kỳ vọng, mốc thật phải luôn nằm gần tâm ảnh, sai số chỉ vài mm ≈ vài chục px).

---

## 4. Thành phần B — Endpoint mới trong `plc_gateway_api_optimized_v2.py`

### 4.1 Import lại logic có sẵn (không copy/paste)
```python
sys.path.insert(0, CALIB_4DIEM_DIR)   # giống cách Do_khoang_cach_kem_camera_v5.py đã làm
from bu_lech_board import (
    ANCHOR_POINTS_POOL, ANCHOR_SETS, board_to_plc,
    kabsch_2d, apply_rigid_offset, load_calibration_matrix,
)
```

### 4.2 Mở rộng `GatewayConfigModel`
```python
fiducial_api_url: str = "http://127.0.0.1:8091/api/detect-marker"
fiducial_confidence_threshold: float = 0.5
camera_fov_width_mm: float = 605.15
camera_fov_height_mm: float = 341.5
camera_image_width_px: int = 1920
camera_image_height_px: int = 1080
camera_axis_matrix: List[List[float]] = [[3.1729, 0.0], [0.0, 3.1622]]  # T khởi tạo từ FOV, sẽ hiệu chỉnh lại bằng /api/calib/camera-axis
max_allowed_pixel_offset_px: float = 150.0   # sanity check
vrs_calib_json_path: str = ".../Calib_4diem/vrs_calib_4diem.json"
offset_runtime_json_path: str = ".../Calib_4diem/offset_runtime.json"
```

### 4.3 Model request/response mới
```python
class AnchorPointResult(BaseModel):
    name: str
    board_xy: List[float]
    expected_plc_xy: List[float]
    detected_pixel_xy: Optional[List[float]] = None
    detection_confidence: Optional[float] = None
    measured_plc_xy: Optional[List[float]] = None
    status: str   # "ok" | "not_found" | "low_confidence" | "outlier_pixel_offset"

class AutoBoardOffsetRequest(PLCFeedbackOverrides):
    anchor_mode: int = 2                 # mặc định 2 điểm (A, C) theo lựa chọn của bạn
    board_id: Optional[str] = None
    plc_pc_ip: str = "192.168.3.101"
    plc_ip: str = "192.168.3.1"
    plc_port: int = 9600
    plc_mem_area: str = "D"
    plc_x_addr: int = 2810
    plc_y_addr: int = 2910
    plc_trigger_addr: int = 3000
    plc_move_timeout_ms: int = 2000
    fiducial_api_url: Optional[str] = None
    fiducial_confidence_threshold: Optional[float] = None
    camera_snapshot_url: Optional[str] = None
    camera_snapshot_timeout_ms: Optional[int] = None

class AutoBoardOffsetResponse(BaseModel):
    success: bool
    message: str
    anchor_mode: int
    anchor_results: List[AnchorPointResult]
    theta_deg: Optional[float] = None
    tx: Optional[float] = None
    ty: Optional[float] = None
    rms_error_mm: Optional[float] = None
    max_error_mm: Optional[float] = None
    offset_saved_path: Optional[str] = None
    timing: Optional[Dict[str, float]] = None
```

### 4.4 Hàm chuyển đổi pixel → mm PLC (module mới hoặc thêm trong file gateway)
```python
def pixel_offset_to_plc_mm(dx_px: float, dy_px: float, axis_matrix: List[List[float]]) -> tuple[float, float]:
    T = np.array(axis_matrix, dtype=float)
    T_inv = np.linalg.inv(T)
    dxdy_mm = T_inv @ np.array([dx_px, dy_px])
    return float(dxdy_mm[0]), float(dxdy_mm[1])
```

### 4.5 Client gọi Fiducial Detector (mirror `AIServiceClient` đã có)
```python
class FiducialClient:
    @staticmethod
    def detect(image_bgr, api_url, confidence_threshold, original_image_bytes=None) -> Optional[Dict]:
        # giống hệt AIServiceClient.detect(), đổi endpoint + payload
        ...
```

### 4.6 Endpoint chính — logic từng bước
```python
@app.post("/api/calib/auto-board-offset", response_model=AutoBoardOffsetResponse)
async def auto_board_offset(request: AutoBoardOffsetRequest):
    coeffs = load_calibration_matrix(GATEWAY_CONFIG["vrs_calib_json_path"])
    anchor_names = ANCHOR_SETS[request.anchor_mode]
    plc = PLCService()
    await asyncio.to_thread(plc.connect, request.plc_pc_ip, request.plc_ip, request.plc_port)

    results, expected_pts, measured_pts = [], [], []
    try:
        for name in anchor_names:
            bx, by = ANCHOR_POINTS_POOL[name]
            ex, ey = board_to_plc(bx, by, coeffs)

            current_x = await asyncio.to_thread(plc.read_float_words, request.plc_mem_area, request.plc_x_addr)
            current_y = await asyncio.to_thread(plc.read_float_words, request.plc_mem_area, request.plc_y_addr)

            await asyncio.to_thread(plc.send_coordinates, request.plc_mem_area,
                                     request.plc_x_addr, request.plc_y_addr,
                                     request.plc_trigger_addr, ex, ey)

            fb = FeedbackConfig.from_request(request)
            fb.apply_dynamic_motion_timeout(current_x, current_y, ex, ey)
            await wait_for_plc_position(plc, request.plc_move_timeout_ms, fb)

            frame = await asyncio.to_thread(camera_service.capture_frame,
                                             resolve_camera_snapshot_timeout_ms(request.camera_snapshot_timeout_ms),
                                             resolve_camera_snapshot_url(request.camera_snapshot_url))
            if frame is None:
                results.append(AnchorPointResult(name=name, board_xy=[bx,by], expected_plc_xy=[ex,ey], status="not_found"))
                continue

            det = await asyncio.to_thread(
                FiducialClient.detect, frame,
                request.fiducial_api_url or GATEWAY_CONFIG["fiducial_api_url"],
                request.fiducial_confidence_threshold or GATEWAY_CONFIG["fiducial_confidence_threshold"],
                camera_service.last_snapshot_image_bytes,
            )
            if not det or not det.get("found"):
                results.append(AnchorPointResult(name=name, board_xy=[bx,by], expected_plc_xy=[ex,ey], status="not_found"))
                continue

            px, py = det["cx"], det["cy"]
            dx_px = px - GATEWAY_CONFIG["camera_image_width_px"] / 2
            dy_px = py - GATEWAY_CONFIG["camera_image_height_px"] / 2

            if hypot(dx_px, dy_px) > GATEWAY_CONFIG["max_allowed_pixel_offset_px"]:
                results.append(AnchorPointResult(name=name, board_xy=[bx,by], expected_plc_xy=[ex,ey],
                                                  detected_pixel_xy=[px,py], status="outlier_pixel_offset"))
                continue

            dx_mm, dy_mm = pixel_offset_to_plc_mm(dx_px, dy_px, GATEWAY_CONFIG["camera_axis_matrix"])
            mx, my = ex + dx_mm, ey + dy_mm

            expected_pts.append((ex, ey))
            measured_pts.append((mx, my))
            results.append(AnchorPointResult(
                name=name, board_xy=[bx, by], expected_plc_xy=[ex, ey],
                detected_pixel_xy=[px, py], detection_confidence=det.get("confidence"),
                measured_plc_xy=[mx, my], status="ok",
            ))

        if len(expected_pts) < 2:
            return AutoBoardOffsetResponse(success=False, message="Không đủ điểm mốc hợp lệ (cần >=2)",
                                            anchor_mode=request.anchor_mode, anchor_results=results)

        R, t, theta, rms, maxerr, residuals = kabsch_2d(expected_pts, measured_pts)

        offset_data = {   # giữ NGUYÊN schema bu_lech_board.py để không phá tương thích ngược
            "anchor_mode": len(expected_pts),
            "theta_deg": math.degrees(theta), "tx": float(t[0]), "ty": float(t[1]),
            "rms_error_mm": rms, "max_error_mm": maxerr,
            "anchor_points_board": [(name, ANCHOR_POINTS_POOL[name]) for name in anchor_names],
            "anchor_points_expected_plc": expected_pts,
            "anchor_points_measured_plc": measured_pts,
            "residuals_mm": residuals.tolist(),
            "board_id": request.board_id,
            "source": "auto_yolo",   # đánh dấu để phân biệt với bản đo tay
        }
        path = GATEWAY_CONFIG["offset_runtime_json_path"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(offset_data, f, indent=4, ensure_ascii=False)

        return AutoBoardOffsetResponse(success=True, message="Tính bù lệch tự động thành công",
                                        anchor_mode=len(expected_pts), anchor_results=results,
                                        theta_deg=math.degrees(theta), tx=float(t[0]), ty=float(t[1]),
                                        rms_error_mm=rms, max_error_mm=maxerr, offset_saved_path=path)
    finally:
        plc.close()
```

### 4.7 Endpoint phụ — hiệu chỉnh T (chạy 1 lần khi setup / khi tháo lắp lại camera)
```python
POST /api/calib/camera-axis
Request: { "delta_mm": 5.0, "anchor_name": "A" }
```
Thực hiện đúng quy trình mục 1 (di chuyển 3 lần, đo pixel, dựng T), trả về T và ghi đè `camera_axis_matrix` vào `plc_gateway_config.json`. Có thể làm CLI script độc lập trước, thêm thành endpoint sau nếu cần thao tác từ xa.

---

## 5. An toàn & xử lý lỗi

- **Không tự động áp dụng offset nếu `rms_error_mm` quá lớn** (ví dụ > 1mm với anchor_mode=3): trả `success=True` nhưng kèm cảnh báo rõ trong `message`, để Flutter/GUI hiển thị cho operator xác nhận thủ công trước khi dùng cho sản xuất — giữ đúng tinh thần cảnh báo residual đã có trong `bu_lech_board.py`.
- **`max_allowed_pixel_offset_px`**: nếu marker phát hiện được nhưng lệch tâm quá xa (ví dụ board gá sai vị trí hoàn toàn, không phải lệch nhẹ vài mm) → đánh dấu `outlier_pixel_offset`, không đưa vào Kabsch, tránh tính ra R/t sai lệch nghiêm trọng.
- **Timeout/PLC không phản hồi**: tái sử dụng nguyên `wait_for_plc_position` + `TimeoutError` đã có, không cần logic mới.
- **Fiducial service không phản hồi** (mạng/service down): trả lỗi rõ ràng, KHÔNG rơi về giá trị mặc định 0,0 (tránh lưu offset sai mà tưởng là đúng).
- **Log đầy đủ từng bước** theo đúng phong cách hiện tại của file gateway (`logger.info` với emoji đánh dấu từng step) để dễ debug khi triển khai thực tế.

---

## 6. Kiểm thử & xác thực (bắt buộc trước khi dùng production)

1. **Unit test `pixel_offset_to_plc_mm`**: với `T` biết trước, cho input giả, kiểm tra output đúng công thức (không cần phần cứng).
2. **Test hiệu chỉnh T**: sau khi hiệu chỉnh, di chuyển PLC một lượng ĐÃ BIẾT (ví dụ +3mm theo X) không thuộc tập điểm dùng để hiệu chỉnh T, đo lại bằng pipeline mới, kiểm tra sai số dự đoán < 0.1-0.2mm.
3. **So sánh với phương pháp thủ công cũ**: trên CÙNG 1 board, chạy cả `bu_lech_board.py` (đo tay) và endpoint mới, so sánh `theta_deg`, `tx`, `ty` — chênh lệch phải nhỏ (ví dụ < 0.05° và < 0.1mm) mới coi là pipeline mới đáng tin.
4. **Test độ lặp lại**: chạy endpoint mới 5-10 lần liên tiếp trên cùng 1 board KHÔNG di chuyển board giữa các lần → offset tính ra phải gần như giống hệt nhau (đánh giá độ ổn định của YOLO + sub-pixel refine).
5. **Test biên**: che marker, tắt đèn, xoay board lệch nhiều (>5mm) để kiểm tra các nhánh lỗi (`not_found`, `outlier_pixel_offset`) hoạt động đúng.

---

## 7. Lộ trình code theo thứ tự (checklist)

1. Thu thập ảnh + gán nhãn dataset vòng tròn mốc (mục 3.2).
2. Train YOLOv8n, xuất `weights/best.pt`, viết `fiducial_service.py` với bước tinh chỉnh sub-pixel (mục 3.3-3.5).
3. Viết script/endpoint hiệu chỉnh **ma trận trục T** (mục 1 + 4.7) — làm trước tiên trên máy thật, vì mọi thứ sau phụ thuộc vào T đúng.
4. Thêm các trường config mới vào `GatewayConfigModel` (mục 4.2).
5. Thêm các Pydantic model mới (mục 4.3) + `pixel_offset_to_plc_mm()` + `FiducialClient` (mục 4.4-4.5).
6. Thêm endpoint `/api/calib/auto-board-offset` (mục 4.6), import lại hàm từ `bu_lech_board.py`.
7. Chạy bộ kiểm thử mục 6, đặc biệt bước 3 (so sánh với đo tay) trước khi thay thế hoàn toàn quy trình cũ.
8. (Tuỳ chọn) Thêm nút "Tự động đo" gọi endpoint mới vào tab "Bu lech Board (Calib)" trong `Do_khoang_cach_kem_camera_v5.py`, giữ song song ô nhập tay như phương án dự phòng.

---

## 8. Tệp mới / tệp sẽ sửa (tóm tắt)

| Tệp | Thay đổi |
|---|---|
| `Fiducial_Detector/` (mới) | Toàn bộ dataset, train script, `fiducial_service.py`, `weights/best.pt` |
| `plc_gateway_api_optimized_v2.py` | Thêm import từ `bu_lech_board.py`, thêm config fields, thêm model, thêm `FiducialClient`, `pixel_offset_to_plc_mm()`, endpoint `/api/calib/auto-board-offset` và `/api/calib/camera-axis` |
| `bu_lech_board.py` | **Không đổi** (chỉ import lại) |
| `offset_runtime.json` | Schema giữ nguyên, thêm 2 field mới `board_id`, `source` (không phá tương thích các nơi đang đọc file này) |
| `Do_khoang_cach_kem_camera_v5.py` | (Tuỳ chọn, bước 8) thêm nút gọi endpoint mới |
