# Hướng dẫn vận hành — Bù lệch board 2 mặt (PA2)

Tài liệu này dành cho **người vận hành máy**, mô tả cách sử dụng hệ thống bù lệch board
tự động trên folder `AutoBoardOffset_YOLO_2Mat` với khả năng hỗ trợ **board 2 mặt (A/B)**.

Folder: `D:\Camera\Dev\AutoBoardOffset_YOLO_2Mat`
Gateway port: **8093** — Fiducial Detector port: **8193**

---

## 1. Tổng quan hoạt động

Hệ thống gồm 3 tiến trình chạy đồng thời:

1. **Camera snapshot service** (Sony streamer) — của hệ thống hiện có, không nằm trong
   folder này. Mặc định tại `http://127.0.0.1:8001/snapshot`.
2. **Fiducial Detector Service** — YOLO phát hiện tâm marker.
3. **PLC Offset Gateway** — điều phối chính: nhận lệnh → nạp calib đúng mặt → di chuyển
   PLC → chụp ảnh → YOLO tìm marker → tính offset Kabsch → lưu file.

Mỗi mặt board (A/B) có **file calib tĩnh riêng** và **file offset runtime riêng**. Khi gọi
bất kỳ endpoint calib nào, bạn truyền `board_side: "A"` hoặc `"B"` để hệ thống tự chọn
đúng cặp file.

---

## 2. Khởi động

Mở 2 terminal riêng trong folder `AutoBoardOffset_YOLO_2Mat`:

```bash
# Terminal 1: Fiducial Detector
cd fiducial_detector
python run_fiducial_service.py
# -> http://localhost:8193

# Terminal 2: PLC Offset Gateway
cd gateway
python run_gateway.py
# -> http://localhost:8093
# -> Swagger UI: http://localhost:8093/docs
```

Kiểm tra nhanh: mở trình duyệt vào `http://localhost:8093` — phải thấy JSON có
`"status": "running"`.

---

## 3. Setup ban đầu (chỉ làm 1 lần)

### 3.1. Đặt file model YOLO

Copy file `.pt` đã train vào `fiducial_detector/weights/fiducial_best.pt` (hoặc sửa
đường dẫn trong `fiducial_service_config.yaml` → `weights_path`).

### 3.2. Hiệu chỉnh ma trận trục camera T

Chỉ cần làm **1 lần** khi setup máy hoặc sau khi tháo/lắp lại camera. Gá board mặt A,
rồi chạy:

```bash
cd tools
python calibrate_camera_axis.py --anchor A --delta-mm 5.0
```

Hoặc gọi API:

```
POST http://localhost:8093/api/calib/camera-axis
{
    "anchor_name": "A",
    "delta_mm": 5.0,
    "board_side": "A"
}
```

Kiểm tra kết quả: `success: true` và `sanity_check: "OK ..."`. Ma trận T được lưu tự
động vào `plc_offset_gateway_config.json`, dùng chung cho cả 2 mặt.

### 3.3. Calib tĩnh mặt A + mặt B (dùng calib_2mat.py)

Tất cả tool calib đã nằm sẵn trong folder `calib/`. Quy trình:

1. Mở file **`calib/calib_2mat.xlsx`** bằng Excel.
2. Cột **Board X**, **Board Y** đã điền sẵn (toạ độ thiết kế, giống nhau 2 mặt).
3. Cột **PLC X (A)**, **PLC Y (A)** — đã có dữ liệu thật mặt A. Nếu cần calib lại:
   gá board mặt A, jog tay PLC tới ≥4 marker, ghi toạ độ PLC đo được vào cột này.
4. Cột **PLC X (B)**, **PLC Y (B)** — **lật board sang mặt B**, jog tay PLC tới ≥4
   marker mặt B (đọc toạ độ thiết kế từ Gerber mặt B — giống Board X/Y), ghi toạ độ
   PLC đo được vào cột này.
5. Lưu file Excel, rồi chạy:

```bash
cd calib
python calib_2mat.py
```

Script sẽ:
- Đọc Excel, tách dữ liệu mặt A và mặt B.
- Fit bilinear cho mỗi mặt có dữ liệu (cột PLC trống → bỏ qua mặt đó).
- Sinh `gateway/vrs_calib_side_a.json` + `gateway/vrs_calib_side_b.json`.
- Tự động cập nhật `calib_paths` trong `gateway/plc_offset_gateway_config.json`.

Kết quả hiển thị trên terminal gồm: condition number, residual từng điểm, cảnh báo
outlier (nếu có). Kiểm tra RMS < 0.05mm là tốt.

Nếu chỉ muốn calib lại 1 mặt: chỉ cần điền/sửa cột PLC của mặt đó, cột mặt kia
để nguyên hoặc xoá — script sinh JSON cho mặt nào có đủ dữ liệu.

Các option thêm:

```bash
python calib_2mat.py --excel my_data.xlsx        # dùng file Excel khác
python calib_2mat.py --no-update-config          # không tự động sửa config gateway
```

---

## 4. Vận hành hàng ngày — Bù lệch board

### 4.1. Mặt A (mặc định)

Mỗi khi **gá board mới** (hoặc tháo ra gá lại):

```
POST http://localhost:8093/api/calib/auto-board-offset
{
    "board_id": "BOARD-2026-07-29-001",
    "board_side": "A",
    "anchor_mode": 2
}
```

Hoặc dùng CLI:

```bash
cd tools
python run_auto_board_offset.py --board-id "BOARD-2026-07-29-001"
```

`anchor_mode`:
- `2` (mặc định) — đo 2 điểm mốc A, C (chéo nhau, nhanh).
- `3` — đo 3 điểm A, C, D (có residual để tự kiểm tra lỗi đo, chính xác hơn).

### 4.2. Mặt B

Lật board sang mặt B, gá vào jig, rồi gọi:

```
POST http://localhost:8093/api/calib/auto-board-offset
{
    "board_id": "BOARD-2026-07-29-001",
    "board_side": "B",
    "anchor_mode": 2
}
```

Hệ thống tự:
- Nạp calib từ `calib_paths.B` (file `vrs_calib_side_b.json`).
- Chạy đo marker → Kabsch.
- Lưu offset vào `offset_paths.B` (hoặc `offset_runtime_side_b.json` mặc định).

### 4.3. Đọc kết quả

Kiểm tra kết quả trả về:

| Trường | Ý nghĩa | Ngưỡng an toàn |
|--------|---------|----------------|
| `success` | `true` = thành công | Phải là `true` |
| `rms_error_mm` | Sai số dư RMS (mm) | < 1.0mm (cảnh báo nếu vượt) |
| `theta_deg` | Góc xoay board so với jig (độ) | Thường < 0.5° |
| `tx`, `ty` | Dịch chuyển X, Y (mm) | Thường < 2mm |
| `anchor_results` | Chi tiết từng điểm mốc | Tất cả `status: "ok"` |

Nếu `rms_error_mm` vượt ngưỡng: offset vẫn được lưu nhưng **nên kiểm tra lại** trước
khi dùng cho sản xuất (đo lại, kiểm tra marker/ánh sáng, hoặc board bị méo).

---

## 5. Xem trước toạ độ PLC đã bù

Trước khi lái máy tới 1 điểm gia công, có thể xem trước toạ độ PLC đã bù:

```
POST http://localhost:8093/api/calib/board-to-plc
{
    "board_x": 100.0,
    "board_y": 200.0,
    "board_side": "A"
}
```

Kết quả trả `plc_nominal_xy` (chưa bù) và `plc_compensated_xy` (đã bù offset).

---

## 6. Kiểm tra trạng thái offset hiện tại

```
GET http://localhost:8093/api/calib/offset-status?board_side=A
GET http://localhost:8093/api/calib/offset-status?board_side=B
```

Trả nội dung file offset runtime cho mặt đã chọn (theta, tx, ty, board_id, timestamp...).

---

## 7. Cấu trúc file dữ liệu

```
gateway/
  vrs_calib_4diem.json          # calib gốc (fallback mặt A nếu calib_paths.A không khai báo)
  vrs_calib_side_a.json         # calib tĩnh mặt A (copy từ calib gốc)
  vrs_calib_side_b.json         # calib tĩnh mặt B (sinh từ quy trình setup mặt B)
  offset_runtime.json           # offset cũ (fallback mặt A nếu offset_paths.A không khai báo)
  offset_runtime_side_a.json    # offset runtime mặt A (ghi bởi auto-board-offset)
  offset_runtime_side_b.json    # offset runtime mặt B (ghi bởi auto-board-offset)
  plc_offset_gateway_config.json  # config chính (calib_paths, offset_paths, T matrix...)
```

---

## 8. Quy trình vận hành đầy đủ (board 2 mặt)

```
Gá board mặt A
    │
    ▼
POST /api/calib/auto-board-offset  { board_side: "A" }
    │
    ▼
Gia công mặt A (dùng offset mặt A)
    │
    ▼
Tháo board, lật sang mặt B, gá lại
    │
    ▼
POST /api/calib/auto-board-offset  { board_side: "B" }
    │
    ▼
Gia công mặt B (dùng offset mặt B)
    │
    ▼
Xong — tháo board
```

Mỗi lần gá lại (kể cả cùng mặt) đều phải chạy lại `auto-board-offset` vì vị trí gá
thay đổi.

---

## 9. Xử lý sự cố thường gặp

### "Chưa khai báo đường dẫn calib cho mặt B" (lỗi 400)

Chưa setup calib mặt B. Thực hiện bước 3.4 ở trên.

### "File calib cho mặt B không tồn tại"

File `vrs_calib_side_b.json` đã khai báo trong config nhưng chưa có trên ổ đĩa. Chạy
quy trình calib tĩnh mặt B để sinh file.

### rms_error_mm cao (> 1.0mm)

- Kiểm tra ánh sáng — marker có bị chói/mờ không?
- Kiểm tra marker — có bị trầy/bẩn/che khuất không?
- Kiểm tra gá board — board có bị xê dịch trong lúc đo không?
- Thử `anchor_mode: 3` (3 điểm) để có thêm dữ liệu kiểm chứng.
- Kiểm tra `anchor_results` — nếu 1 điểm `status != "ok"`, vấn đề nằm ở điểm đó.

### Fiducial Detector không tìm thấy marker (status: "not_found")

- Camera có chụp được ảnh rõ không? Test: `GET http://localhost:8093/api/test-camera`
- Model YOLO có đúng không? Test: `GET http://localhost:8093/api/test-fiducial`
- PLC có di chuyển tới đúng vị trí không? Test:
  `GET http://localhost:8093/api/calib/anchor-info?anchor_name=A&board_side=A`
  rồi so sánh `plc_expected_xy` với vị trí thực tế của marker.

### Gateway không khởi động được

- Kiểm tra port 8093 có bị tiến trình khác chiếm không.
- Kiểm tra `plc_offset_gateway_config.json` có bị lỗi JSON không (mở trong Notepad kiểm
  tra dấu phẩy, ngoặc).

---

## 10. Các endpoint API chính

| Endpoint | Method | Mô tả |
|----------|--------|-------|
| `/api/calib/auto-board-offset` | POST | Tự động đo + tính bù lệch board (param: `board_side`) |
| `/api/calib/camera-axis` | POST | Hiệu chỉnh ma trận T (1 lần khi setup) |
| `/api/calib/board-to-plc` | POST | Xem trước toạ độ PLC đã bù (param: `board_side`) |
| `/api/calib/offset-status` | GET | Đọc offset hiện tại (query: `?board_side=A`) |
| `/api/calib/anchor-info` | GET | Xem toạ độ điểm mốc (query: `?anchor_name=A&board_side=A`) |
| `/api/camera-config` | GET/PUT | Đọc/sửa config gateway |
| `/api/test-camera` | GET | Test chụp ảnh camera |
| `/api/test-fiducial` | GET | Test phát hiện marker (chụp + YOLO) |
| `/api/plc/move` | POST | Di chuyển PLC tới toạ độ X,Y (test tay) |

Swagger UI đầy đủ: `http://localhost:8093/docs`

---

## 11. Các điểm mốc (anchor) có sẵn

| Tên | Board X (mm) | Board Y (mm) | Ghi chú |
|-----|-------------|-------------|---------|
| A | 9.9 | 10.0 | Góc trái dưới |
| B | 9.9 | 394.0 | Góc trái trên |
| C | 596.6 | 393.0 | Góc phải trên |
| D | 597.9 | 10.0 | Góc phải dưới |
| E | 69.0 | 8.0 | Bổ sung |
| F | 534.5 | 396.0 | Bổ sung |

Mặc định `anchor_mode: 2` dùng **A + C** (chéo nhau), `anchor_mode: 3` dùng **A + C + D**.

Toạ độ này giống nhau giữa 2 mặt (vì Gerber ghi cùng số), nhưng calib tĩnh riêng cho
mỗi mặt sẽ quy đổi ra toạ độ PLC khác nhau.

---

## 12. Lưu ý quan trọng

- **Ma trận T dùng chung** cho cả 2 mặt — chỉ cần hiệu chỉnh 1 lần, không phân biệt mặt.
- **Calib tĩnh riêng cho mỗi mặt** — đây là điểm khác biệt chính của PA2 so với hệ thống
  cũ (không dùng công thức lật gương nữa).
- **Offset runtime riêng cho mỗi mặt** — mỗi lần gá lại board (bất kỳ mặt nào) phải chạy
  lại `auto-board-offset` cho mặt đó.
- **Không cần cấu hình mirror** — các tham số `board_side_b_mirror_axis` và
  `board_side_b_mirror_reference_mm` đã bị loại bỏ. Nếu config cũ còn chứa chúng, gateway
  sẽ tự bỏ qua.
- Nếu chỉ dùng **mặt A**, hệ thống hoạt động giống hệt trước — không cần thay đổi gì.
