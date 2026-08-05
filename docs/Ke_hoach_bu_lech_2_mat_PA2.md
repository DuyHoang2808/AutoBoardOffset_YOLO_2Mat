# Kế hoạch: Bù lệch board 2 mặt (A/B) — PA2: calib riêng từng mặt

> Trạng thái: **ĐÃ CODE** (bước 1-3, 5-6 trong lộ trình mục 10). Bước 4 (sinh
> `vrs_calib_side_b.json` thủ công trên máy thật) và bước 7 (Phase 2: auto sinh calib) chưa
> thực hiện — đợi calib tĩnh thật trên máy.
> Chốt phương án = PA2 (mỗi mặt một calib tĩnh + một offset riêng, anchor dùng chung bộ số,
> định tuyến theo `board_side`, bỏ hẳn công thức lật gương).

## 1. Bối cảnh & lý do chọn PA2

- Lật board hiện tại: **tháo ra, lật tay, gá lại thủ công** (tương lai sẽ có cơ cấu lật tự
  động). Mỗi lần gá là một lần đặt độc lập → **offset gá phải chạy riêng cho từng mặt** (auto
  pipeline đã lo).
- Mặt B có **Gerber riêng, hệ toạ độ riêng**: gốc dời từ dưới (mặt A) lên trên (mặt B), trục
  bị lật (khả năng kèm đảo handedness). Con số toạ độ giống nhau giữa 2 Gerber (vd điểm A =
  (9.9, 10) ở cả hai) nhưng trỏ tới vị trí vật lý khác nhau.
- Vì gốc dời + trục lật, KHÔNG thể dùng calib mặt A cho toạ độ mặt B. Công thức lật gương
  (PA1) phải mô hình hoá cùng lúc dời gốc + lật trục + đoán `reference` → mong manh.
- PA2 giải quyết triệt để: **calib tĩnh riêng cho mặt B** tự mã hoá dời gốc + lật trục +
  handedness. Không cần toán lật. Offset Kabsch mỗi board vẫn là rigid transform thuần (phần
  reflection đã nằm trong calib tĩnh) nên không phát sinh vấn đề dấu ở tầng offset.

## 2. Nguyên tắc thiết kế

1. **Ma trận trục camera `T` DÙNG CHUNG** cho cả 2 mặt (camera↔máy không liên quan việc lật
   board). Không tách T theo mặt.
2. **Anchor dùng chung bộ số** `ANCHOR_POINTS_POOL` (đã xác nhận số giống nhau 2 mặt). Chỉ
   calib khác nhau. (Chừa sẵn khả năng override anchor theo mặt nếu sau này khác.)
3. **Mỗi mặt: 1 file calib tĩnh + 1 file offset runtime riêng.**
4. `board_side` ∈ {A, B} là **khoá định tuyến**: chọn đúng cặp {calib, offset} (+ anchor nếu
   override).
5. **Bỏ** cấu hình lật gương cũ (`board_side_b_mirror_axis`, `board_side_b_mirror_reference_mm`)
   để tránh nhầm lẫn.
6. **Tương thích ngược**: mặt A giữ nguyên hành vi/file hiện tại; thêm mặt B là mở rộng.

## 3. Thay đổi cấu hình (gateway config)

Thêm bản đồ đường dẫn theo mặt, có fallback về key cũ cho mặt A:

```jsonc
// plc_offset_gateway_config.json (đề xuất)
"calib_paths": {
  "A": "D:\\...\\vrs_calib_side_a.json",
  "B": "D:\\...\\vrs_calib_side_b.json"
},
"offset_paths": {
  "A": "D:\\...\\offset_runtime_side_a.json",
  "B": "D:\\...\\offset_runtime_side_b.json"
}
// Giữ lại vrs_calib_json_path / offset_runtime_json_path (cũ) làm fallback cho mặt A
// nếu calib_paths/offset_paths không khai báo mặt A -> không phá tích hợp v6 hiện có.
```

Bỏ 2 key: `board_side_b_mirror_axis`, `board_side_b_mirror_reference_mm`.

Giữ nguyên: `camera_axis_matrix`, `camera_axis_calibrated`, `camera_axis_invert_x/y`,
`camera_fov_*`, `camera_image_*`, ngưỡng an toàn.

## 4. Hàm/logic sẽ đổi trong gateway (mô tả, chưa code)

- **Resolver theo mặt**: `resolve_calib_path(side)` và `resolve_offset_path(side)` — đọc
  `calib_paths[side]`, nếu thiếu và side=="A" thì dùng key cũ; nếu thiếu mặt B → trả lỗi 400
  rõ ràng ("chưa khai báo calib cho mặt B").
- **`validate_board_side(side)`**: chỉ còn kiểm tra side ∈ {A,B} + tồn tại file calib cho
  side đó. Bỏ toàn bộ yêu cầu cấu hình mirror.
- **Xoá** `mirror_board_xy_for_side()` và mọi chỗ gọi nó. Anchor board coord dùng trực tiếp
  (không lật).
- **`/api/calib/auto-board-offset`**:
  - Nạp calib theo `resolve_calib_path(board_side)`.
  - Với mỗi anchor: `expected = board_to_plc(anchor_xy, calib_side)`; di chuyển; detect;
    `measured = measured_plc_from_marker(...)` (giữ nguyên bản đã sửa dấu + cờ invert).
  - Kabsch → lưu vào `resolve_offset_path(board_side)`; ghi thêm `board_side` vào file offset.
- **`/api/calib/camera-axis`**: T lưu chung (không đổi), nhưng P0 = `board_to_plc(anchor,
  calib_side)` nên vẫn cần chọn calib theo `board_side` (thường hiệu chỉnh T trên mặt A).
- **`/api/calib/board-to-plc`** (preview) và **`/api/calib/offset-status`**: thêm tham số
  `board_side` để chọn đúng calib/offset file.

## 5. Sinh calib tĩnh cho mặt B (một lần lúc setup)

Hai lựa chọn:

- **5a (Phase 1, dùng ngay)**: dùng đúng quy trình calib tĩnh hiện có (`calculate_4diem.py` /
  Excel board↔PLC). Lật board sang mặt B, jog tới ≥4 marker mặt B, đọc design coord từ Gerber
  mặt B, ghi PLC đo được → chạy tool sinh `vrs_calib_side_b.json`. Không cần code mới.
- **5b (Phase 2, tuỳ chọn - tự động hoá)**: thêm endpoint `/api/calib/auto-generate-calib`
  di chuyển tới ≥4 marker mặt B (toạ độ kỳ vọng thô lấy từ FOV/ước lượng ban đầu), detect,
  tính PLC thực của từng marker, rồi FIT ma trận bilinear đầy đủ (không chỉ rigid offset) →
  sinh `vrs_calib_side_b.json` tự động. Đây là mở rộng lớn hơn, làm sau nếu cần.

Khuyến nghị: Phase 1 trước (nhanh, chắc), cân nhắc 5b sau.

## 6. Bố cục file dữ liệu (đề xuất)

```
AutoBoardOffset_YOLO/gateway/
  vrs_calib_side_a.json        # = calib mặt A (có thể trỏ về Calib_4diem/vrs_calib_4diem.json)
  vrs_calib_side_b.json        # calib mặt B (sinh ở bước 5)
  offset_runtime_side_a.json   # offset auto mặt A
  offset_runtime_side_b.json   # offset auto mặt B
```

## 7. Định tuyến hạ nguồn (v6 / lệnh move)

- `board_side` chọn cặp {calib, offset} khi tính điểm PLC đã bù cho một điểm gia công.
- Nếu tích hợp v6: v6 cần biết đang chạy mặt nào để nạp đúng file offset+calib (qua tham số
  hoặc một ô chọn A/B trên UI). Sẽ bàn khi làm tích hợp.

## 8. Điểm cần kiểm chứng trên máy thật

- Calib mặt B: residual nhỏ khi sinh; định thức (determinant) của calib B có thể ngược dấu so
  với A (do reflection khi lật) — bình thường, không phải lỗi.
- Chạy auto-offset mặt B: rms nhỏ; lái thử tới 1 feature mặt B đã biết → không lệch.
- Xác nhận `T` dùng chung vẫn đúng cho mặt B (camera không đổi).

## 9. Kế hoạch kiểm thử (không cần phần cứng)

- Test resolver: `board_side` A/B → đúng đường dẫn calib/offset; thiếu calib B → lỗi 400.
- Test auto-offset mặt B (mock): với calib_B giả + detections giả, kiểm tra expected/measured/
  offset tính đúng và lưu vào `offset_runtime_side_b.json` (có trường `board_side="B"`).
- Test bảo đảm KHÔNG còn toán lật gương (mirror) trong luồng.
- Test hồi quy: hành vi mặt A không đổi.

## 10. Lộ trình triển khai (khi bắt tay code)

1. Config: thêm `calib_paths` / `offset_paths` (+ fallback mặt A), bỏ mirror keys.
2. Resolver + `validate_board_side` rút gọn; xoá `mirror_board_xy_for_side`.
3. Refactor 4 endpoint (auto-board-offset, camera-axis, board-to-plc, offset-status) dùng
   resolver theo mặt.
4. Sinh `vrs_calib_side_b.json` (Phase 1, thủ công 1 lần).
5. Viết test mục 9; chạy pytest.
6. Cập nhật README + báo cáo kỹ thuật.
7. (Tuỳ chọn) Phase 2: endpoint auto sinh calib mặt B.

## 11. Việc KHÔNG làm trong PA2 (để tránh hiểu nhầm)

- Không lật gương toạ độ bằng công thức.
- Không tách `T` theo mặt.
- Không đụng thuật toán Kabsch / `bu_lech_board.py`.
- Không tự động sinh calib mặt B ở Phase 1 (làm thủ công 1 lần bằng tool có sẵn).
