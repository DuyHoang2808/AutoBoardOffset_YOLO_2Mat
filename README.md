# AutoBoardOffset_YOLO_2Mat

Tự động hoá bước bù lệch board (thay thế `bu_lech_board.py` đo tay) bằng YOLO: PLC di
chuyển tới điểm mốc → chụp ảnh → YOLO tìm tâm marker (tròn hoặc vuông) → suy ra độ lệch →
Kabsch → lưu offset. Đây là bản **clone độc lập, tách folder hoàn toàn** — không đọc/ghi
gì vào code hay dữ liệu cũ, để phát triển tiếp tính năng **board 2 mặt (A/B)** mà không
ảnh hưởng phần đang chạy:

- Bản trước `AutoBoardOffset_YOLO/` (chỉ hỗ trợ 1 mặt) — **giữ nguyên, không đụng tới**,
  vẫn dùng được nếu cần.
- Bản GUI gốc `Calib_Phan_Cung_VRS/Do_Khoang_Cach/Do_khoang_cach_kem_camera_v3..v6.py` —
  **giữ nguyên, không đụng tới**. Folder này có bản **copy riêng** của cả chuỗi v3→v6
  trong `gui/` (xem mục "Bản GUI riêng" bên dưới), tự trỏ vào `gateway/` của chính nó.

Xem `docs/Ke_hoach_tu_dong_bu_lech_board.md` (kế hoạch gốc) và
`docs/Ke_hoach_bu_lech_2_mat_PA2.md` (kế hoạch board 2 mặt — **ĐÃ CODE**, mỗi mặt calib tĩnh
riêng, không dùng công thức lật gương).

## Cấu trúc

```
AutoBoardOffset_YOLO_2Mat/
  gateway/
    plc_offset_gateway.py      # FastAPI chính (port 8093) - PLC + camera + endpoint bù lệch
    bu_lech_board.py           # copy nguyên vẹn từ dự án gốc (Kabsch, board_to_plc...)
    vrs_calib_4diem.json       # copy ma trận calib gốc (bilinear Board<->PLC)
    ClassLibrary.dll           # copy PLC DLL (pythonnet) để gateway tự chạy được
    run_gateway.py             # entry point (uvicorn)
  fiducial_detector/
    fiducial_service.py        # FastAPI riêng (port 8193) - YOLO phát hiện tâm marker
    run_fiducial_service.py    # entry point
    weights/                   # ĐẶT FILE .pt CỦA BẠN VÀO ĐÂY (xem weights/PLACE_MODEL_HERE.txt)
  calib/                       # SINH MA TRẬN CALIB TĨNH cho board 2 mặt
    calib_2mat.py              # script chính: đọc Excel → sinh vrs_calib_side_a/b.json
    calib_2mat.xlsx            # Excel mẫu (1 sheet, cột PLC cho cả mặt A & B)
    Calib_4diem.py             # copy từ gốc (dùng riêng nếu cần)
    calculate_4diem.py         # copy từ gốc (tra cứu toạ độ PLC)
  tools/
    calibrate_camera_axis.py   # CLI hiệu chỉnh ma trận trục T (chạy 1 lần khi setup)
    run_auto_board_offset.py   # CLI chạy bù lệch board tự động (mỗi khi đổi board)
  gui/                         # BẢN COPY RIÊNG của Do_khoang_cach_kem_camera_v3..v6.py
    Do_khoang_cach_kem_camera_v3.py .. v6.py   # y hệt bản gốc, chỉ sửa lại path tự trỏ nội bộ
    Funs/test_plc_gateway_gui_v2_with_board_offset.py
  Giao_dien_camera/            # copy riêng (camera.py/ui.py/main.py) - v3 trong gui/ cần
  tests/                       # unit test, KHÔNG cần phần cứng/model thật
  docs/                        # kế hoạch kỹ thuật (gồm cả PA2 - board 2 mặt)
  requirements.txt
```

## Cài đặt

```bash
cd AutoBoardOffset_YOLO_2Mat
pip install -r requirements.txt
```

`pythonnet` chỉ hoạt động thật trên Windows (cần .NET) — trên máy không có PLC/camera
(vd. để đọc code/chạy test), gateway vẫn chạy được ở "mock mode" (giống hệt cơ chế mock
đã có sẵn trong code gốc).

## Chạy

Cần chạy **3 tiến trình riêng** (đúng như thiết kế: gateway PLC và YOLO detector tách
biệt hoàn toàn):

```bash
# 1. Camera snapshot service (Sony streamer) - của hệ thống hiện có, không nằm trong folder này

# 2. Fiducial Detector Service (YOLO riêng)
cd fiducial_detector
python run_fiducial_service.py         # http://localhost:8193

# 3. PLC Offset Gateway (chính)
cd gateway
python run_gateway.py                  # http://localhost:8093, xem docs tại /docs
```

Muốn chạy thử GUI (bản copy riêng, không đụng bản gốc):

```bash
cd gui
python Do_khoang_cach_kem_camera_v6.py
```

## Việc BẠN cần làm trước khi dùng thật (theo đúng thứ tự)

1. **Đặt file model YOLO (.pt)** vào `fiducial_detector/weights/fiducial_best.pt` (xem
   `weights/PLACE_MODEL_HERE.txt` để biết cách đổi tên/đường dẫn/lọc theo class). Cho tới
   lúc đó, service chạy "mock mode" - vẫn khởi động và trả lời đúng contract API
   (`found: false` + message rõ ràng), toàn bộ phần còn lại đã viết/test xong sẵn.
2. **Hiệu chỉnh ma trận trục camera↔máy T** (chỉ làm 1 LẦN khi setup máy hoặc sau khi
   tháo/lắp lại camera):
   ```bash
   cd tools
   python calibrate_camera_axis.py --anchor A --delta-mm 5.0
   ```
   Script này DI CHUYỂN MÁY THẬT 3 lần để đo, rồi lưu `camera_axis_matrix` vào
   `gateway/plc_offset_gateway_config.json`. Trước khi chạy bước 3, hãy chắc `success:
   true` và đọc kỹ `sanity_check` trong kết quả trả về.
3. **Chạy bù lệch board mỗi khi gá board mới:**
   ```bash
   cd tools
   python run_auto_board_offset.py --board-id "BOARD-2026-07-14-001"
   ```
   hoặc gọi thẳng `POST /api/calib/auto-board-offset` (xem `/docs` để có UI thử API).
   Kết quả (theta_deg, tx, ty, rms_error_mm...) được lưu vào
   `gateway/offset_runtime.json` — **cùng schema** với `bu_lech_board.py` gốc, chỉ thêm 2
   field `board_id` và `source: "auto_yolo"`.

## Kiểm thử (không cần phần cứng)

```bash
cd tests
pip install pytest httpx
pytest -q
```

38 test hiện có kiểm tra:
- Toán học quy đổi pixel↔mm qua ma trận T (`test_pixel_math.py`), bao gồm cả trường hợp
  camera lắp lật trục / xoay lệch.
- Hồi quy: `bu_lech_board.py` + `vrs_calib_4diem.json` đã copy sang đây vẫn cho ra ĐÚNG
  kết quả như số liệu thật đã có trong dự án gốc (`test_bu_lech_regression.py`).
- Logic chọn candidate + tinh chỉnh sub-pixel + mock mode của Fiducial Detector
  (`test_fiducial_parsing.py`), dùng ảnh tổng hợp (không cần model .pt thật).
- PA2 resolver: `resolve_calib_path`/`resolve_offset_path` trả đúng đường dẫn theo mặt A/B,
  fallback mặt A, lỗi 400 khi thiếu calib mặt B; `validate_board_side` kiểm tra tồn tại file;
  xác nhận `mirror_board_xy_for_side` + mirror config keys đã bị xoá (`test_board_side_mirror.py`).

**Những gì CHƯA thể kiểm thử ở đây** (cần phần cứng thật trên máy của bạn, xem mục 6 của
`docs/Ke_hoach_tu_dong_bu_lech_board.md` để có quy trình đầy đủ):
- Độ chính xác thật của model YOLO + tinh chỉnh sub-pixel trên ảnh marker thật.
- Hiệu chỉnh ma trận T thật (cần PLC + camera thật).
- So sánh kết quả `auto-board-offset` với kết quả đo tay `bu_lech_board.py` trên CÙNG 1
  board thật.
- Độ lặp lại (chạy nhiều lần liên tiếp không di chuyển board).

## Các cổng (port) đã dùng (khác với production VÀ khác với AutoBoardOffset_YOLO bản trước,
để chạy song song không đụng độ với bất kỳ tiến trình nào đang chạy)

| Service | Port | Ghi chú |
|---|---|---|
| PLC Offset Gateway (`gateway/`) | 8093 | Production/GUI cũ dùng 8083; bản `AutoBoardOffset_YOLO` (v1) cũng dùng 8083 |
| Fiducial Detector Service | 8193 | AI Detection API (lỗi board) gốc dùng 8082; bản v1 dùng 8191 |

`gui/Funs/test_plc_gateway_gui_v2_with_board_offset.py` (bản copy riêng) đã đổi
`DEFAULT_BASE_URL` sang `http://localhost:8093` để tab "PLC Gateway Tester" trong
`gui/` mặc định nói chuyện với đúng gateway của **folder này**, không vô tình gọi
sang gateway khác đang chạy ở 8083.

## Các endpoint chính (xem `/docs` để có Swagger UI đầy đủ)

| Endpoint | Method | Mô tả |
|---|---|---|
| `/api/calib/auto-board-offset` | POST | Tự động đo + tính bù lệch board (thay bu_lech_board.py thủ công) |
| `/api/calib/camera-axis` | POST | Hiệu chỉnh ma trận T (1 lần khi setup) |
| `/api/calib/board-to-plc` | POST | Xem trước toạ độ PLC đã bù lệch cho 1 điểm Board X/Y |
| `/api/calib/offset-status` | GET | Đọc offset_runtime.json hiện tại |
| `/api/plc/move`, `/api/test-plc`, `/api/test-camera`, `/api/test-fiducial` | - | Test tay từng phần |

## Marker hình tròn hay hình vuông? (shape-agnostic)

Bước tinh chỉnh sub-pixel dùng **trọng tâm (centroid) qua `cv2.moments`**, không phụ thuộc
hình dạng — hoạt động chính xác với **cả hình tròn lẫn hình vuông**. Đổi loại marker chỉ cần:
train lại file `.pt` trên marker mới và (nếu model nhiều class) sửa `class_name_filter` trong
`fiducial_service_config.yaml`. **Không phải sửa code gateway.**

## Board 2 mặt (PA2) — calib tĩnh riêng từng mặt

Mỗi mặt board (A/B) có **file calib tĩnh riêng** + **file offset runtime riêng**. Không
dùng công thức lật gương — calib mặt B tự mã hoá dời gốc + lật trục + handedness.

Cấu hình trong `plc_offset_gateway_config.json` (hoặc `PUT /api/camera-config`):

```jsonc
"calib_paths": {
  "A": "D:\\...\\vrs_calib_side_a.json",
  "B": "D:\\...\\vrs_calib_side_b.json"    // thêm khi đã calib mặt B
},
"offset_paths": {
  "A": "D:\\...\\offset_runtime_side_a.json",
  "B": "D:\\...\\offset_runtime_side_b.json"
}
```

Nếu `calib_paths` không khai báo mặt A → fallback về `vrs_calib_json_path` cũ (tương thích
ngược). Nếu thiếu mặt B → gateway trả lỗi 400 rõ ràng. Ma trận trục camera `T` dùng chung
cho cả 2 mặt (camera không liên quan việc lật board).

Quy trình setup mặt B (1 lần): lật board sang mặt B, jog tới ≥4 marker mặt B, đọc design
coord từ Gerber mặt B, chạy calib tĩnh → sinh `vrs_calib_side_b.json`, khai báo đường dẫn
vào `calib_paths.B`.

Sử dụng: truyền `board_side: "B"` trong request `/api/calib/auto-board-offset` (và các
endpoint calib khác). Kết quả offset được lưu riêng vào `offset_runtime_side_b.json`.

Xem chi tiết kỹ thuật: `docs/Ke_hoach_bu_lech_2_mat_PA2.md`.

## Đảo dấu trục camera (khi camera lắp lật trục so với PLC)

Nếu bạn KHÔNG chạy hiệu chỉnh T đầy đủ (dùng ma trận mặc định đường chéo dương suy từ FOV)
mà camera lại lắp lật trục, chỉ cần bật cờ trong config thay vì sửa tay ma trận:

- `camera_axis_invert_x: true` — đảo dấu độ lệch mm theo trục X.
- `camera_axis_invert_y: true` — đảo dấu độ lệch mm theo trục Y.

Cách áp: sau khi quy đổi pixel→mm, độ lệch của trục bật cờ sẽ được đổi dấu. Chỉnh trong
`plc_offset_gateway_config.json` hoặc `PUT /api/camera-config`.

**Quan trọng:** nếu đã hiệu chỉnh T bằng `/api/calib/camera-axis` thì T đã chứa sẵn dấu
đúng (kể cả trục âm) — **để cả hai cờ = false**, bật thêm sẽ bị đảo dấu 2 lần → bù sai hướng.
Hai cờ này chỉ là lối tắt cho trường hợp dùng ma trận mặc định chưa calib.

## Giả định / điều đã tự quyết định khi code (nói rõ để bạn kiểm tra lại)

- Camera snapshot mặc định: `http://127.0.0.1:8001/snapshot` (lấy đúng theo config đang
  deploy thật của gateway gốc, không phải giá trị mặc định 9000 trong code gốc).
- PLC mặc định: `pc_ip=192.168.3.101, plc_ip=192.168.3.1, port=9600`, địa chỉ thanh ghi
  X/Y/Trigger giống hệt gateway gốc (2810/2910/3000).
- `max_allowed_pixel_offset_px = 150` và `max_allowed_rms_error_mm = 1.0`: ngưỡng an toàn
  tự chọn, có thể chỉnh qua `PUT /api/camera-config` hoặc field trong config JSON.
- `gui/` là bản copy độc lập của `Do_khoang_cach_kem_camera_v3..v6.py` (tab "PLC Gateway
  Tester" đã trỏ sang `gateway/` của chính folder này qua port 8093) — nhưng CHƯA nối
  thêm UI cho `board_side`/PA2 vào đây; đây là bước tiếp theo.
