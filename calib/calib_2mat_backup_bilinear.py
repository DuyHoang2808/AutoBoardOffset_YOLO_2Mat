"""
calib_2mat.py — Sinh ma trận calib tĩnh cho board 2 mặt (A/B) từ 1 file Excel.
(BẢN BACKUP — chỉ dùng bilinear 4 params, trước khi nâng cấp biquadratic)

Đọc file Excel (sheet "Calibration_Data") có bố cục:
    Điểm | Board X | Board Y | PLC X (A) | PLC Y (A) | PLC X (B) | PLC Y (B)

- Board X/Y giống nhau cho cả 2 mặt (toạ độ thiết kế từ Gerber).
- PLC X/Y đo được trên máy thật, riêng cho từng mặt.
- Cột PLC mặt nào để trống → bỏ qua mặt đó (chỉ sinh JSON cho mặt có dữ liệu).

Output (mặc định - xem gateway/products_registry.yaml, calib_dir của mã hàng đang production):
    gateway/products/<mã_hàng>/vrs_calib_side_a.json   (nếu có dữ liệu mặt A)
    gateway/products/<mã_hàng>/vrs_calib_side_b.json   (nếu có dữ liệu mặt B)

KHÔNG cần cập nhật gì thêm trong plc_offset_gateway_config.json - gateway tự đọc file này
theo đúng tên/thư mục quy ước (calib_dir của mã hàng đang active), miễn ghi đúng --output-dir.

Cách dùng:
    cd AutoBoardOffset_YOLO_2Mat/calib
    python calib_2mat.py                          # mặc định đọc calib_2mat.xlsx
    python calib_2mat.py --excel my_data.xlsx     # chỉ định file khác
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd


# ==============================================================================
# Core: fit bilinear model (giống Calib_4diem.py gốc, tách hàm rõ ràng)
# ==============================================================================

def fit_bilinear(board_x: np.ndarray, board_y: np.ndarray,
                 plc_x: np.ndarray, plc_y: np.ndarray) -> dict | None:
    """Fit mô hình bilinear: PLC = c0 + c1*x + c2*y + c3*x*y.

    Trả dict hệ số + _diagnostics, hoặc None nếu dữ liệu không hợp lệ.
    """
    n = len(board_x)
    if n < 4:
        print(f"  LỖI: Chỉ có {n} điểm, cần tối thiểu 4.")
        return None

    A = np.column_stack([np.ones(n), board_x, board_y, board_x * board_y])

    # Condition number trên toạ độ chuẩn hoá
    scale = max(np.abs(board_x).max(), np.abs(board_y).max(), 1e-9)
    xn, yn = board_x / scale, board_y / scale
    A_diag = np.column_stack([np.ones(n), xn, yn, xn * yn])
    cond_number = float(np.linalg.cond(A_diag))

    if cond_number > 1000:
        print(f"  CẢNH BÁO: Condition number = {cond_number:.1f} — điểm gần thẳng hàng!")
    elif cond_number > 100:
        print(f"  Lưu ý: Condition number = {cond_number:.1f} — điểm chưa phân bố lý tưởng.")
    else:
        print(f"  Condition number = {cond_number:.1f} — OK.")

    try:
        c_coeffs, _, rank, _ = np.linalg.lstsq(A, plc_x, rcond=None)
        d_coeffs, _, _, _ = np.linalg.lstsq(A, plc_y, rcond=None)
    except np.linalg.LinAlgError:
        print("  LỖI: Ma trận không hợp lệ (điểm trùng/thẳng hàng).")
        return None

    if rank < 4:
        print(f"  LỖI: Ma trận thiếu hạng (rank {rank}/4). Đo lại điểm khác vị trí.")
        return None

    # Residuals
    pred_x = A @ c_coeffs
    pred_y = A @ d_coeffs
    residuals = np.sqrt((pred_x - plc_x) ** 2 + (pred_y - plc_y) ** 2)
    rms_error = float(np.sqrt(np.mean(residuals ** 2)))
    max_error = float(np.max(residuals))

    print(f"  Residual: RMS = {rms_error:.4f} mm, Max = {max_error:.4f} mm")
    for i, res in enumerate(residuals):
        flag = ""
        if n > 4 and res > 2 * rms_error and res > 0.02:
            flag = "  <-- NGHI NGỜ outlier!"
        print(f"    Điểm {i+1} (Board {board_x[i]:.3f}, {board_y[i]:.3f}): {res:.4f} mm{flag}")

    return {
        "c0": float(c_coeffs[0]), "c1": float(c_coeffs[1]),
        "c2": float(c_coeffs[2]), "c3": float(c_coeffs[3]),
        "d0": float(d_coeffs[0]), "d1": float(d_coeffs[1]),
        "d2": float(d_coeffs[2]), "d3": float(d_coeffs[3]),
        "_diagnostics": {
            "n_points": n,
            "condition_number": cond_number,
            "rms_residual_mm": rms_error,
            "max_residual_mm": max_error,
        },
    }


# ==============================================================================
# Đọc Excel 2 mặt
# ==============================================================================

def read_excel_2mat(excel_path: str) -> tuple[list, list]:
    """Đọc Excel, trả (rows_side_a, rows_side_b).

    Mỗi row = (board_x, board_y, plc_x, plc_y). Chỉ trả row có đủ 4 số.
    """
    df_raw = pd.read_excel(excel_path, sheet_name="Calibration_Data", header=None)

    # Tìm dòng tiêu đề chứa "Board X"
    header_row_idx = None
    for idx, row in df_raw.iterrows():
        row_str = [str(v) for v in row.values]
        if any("Board X" in s for s in row_str):
            header_row_idx = idx
            break

    if header_row_idx is None:
        raise ValueError("Không tìm thấy dòng tiêu đề chứa 'Board X' trong sheet 'Calibration_Data'!")

    headers = [str(c).strip() for c in df_raw.iloc[header_row_idx].values]

    # Tìm index cột
    def find_col(keyword):
        matches = [i for i, h in enumerate(headers) if keyword in h]
        return matches[0] if matches else None

    idx_bx = find_col("Board X")
    idx_by = find_col("Board Y")

    # Tìm cột PLC cho mặt A và B
    plc_x_cols = [i for i, h in enumerate(headers) if "PLC X" in h]
    plc_y_cols = [i for i, h in enumerate(headers) if "PLC Y" in h]

    if len(plc_x_cols) < 1 or len(plc_y_cols) < 1:
        raise ValueError("Không tìm thấy cột 'PLC X' / 'PLC Y' trong tiêu đề!")

    # Cột PLC đầu tiên = mặt A, cột thứ 2 (nếu có) = mặt B
    idx_pxa, idx_pya = plc_x_cols[0], plc_y_cols[0]
    idx_pxb = plc_x_cols[1] if len(plc_x_cols) > 1 else None
    idx_pyb = plc_y_cols[1] if len(plc_y_cols) > 1 else None

    rows_a, rows_b = [], []

    for offset in range(1, min(200, len(df_raw) - header_row_idx)):
        row = df_raw.iloc[header_row_idx + offset].values

        def to_num(idx):
            if idx is None:
                return float("nan")
            v = pd.to_numeric(pd.Series([row[idx]]), errors="coerce").iloc[0]
            return v

        bx, by = to_num(idx_bx), to_num(idx_by)
        if np.isnan(bx) and np.isnan(by):
            break  # hết dữ liệu

        pxa, pya = to_num(idx_pxa), to_num(idx_pya)
        if not (np.isnan(pxa) or np.isnan(pya)):
            rows_a.append((bx, by, pxa, pya))

        pxb, pyb = to_num(idx_pxb), to_num(idx_pyb)
        if not (np.isnan(pxb) or np.isnan(pyb)):
            rows_b.append((bx, by, pxb, pyb))

    return rows_a, rows_b


# ==============================================================================
# Cập nhật gateway config
# ==============================================================================

# ==============================================================================
# Main
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Sinh ma trận calib tĩnh cho board 2 mặt (A/B) từ 1 file Excel."
    )
    parser.add_argument("--excel", default=None,
                        help="Đường dẫn file Excel (mặc định: calib_2mat.xlsx cùng thư mục)")
    parser.add_argument("--output-dir", default=None,
                        help="Thư mục xuất JSON (mặc định: calib_dir của mã hàng đang production)")
    args = parser.parse_args()

    this_dir = Path(__file__).resolve().parent
    project_dir = this_dir.parent

    excel_path = args.excel or str(this_dir / "calib_2mat.xlsx")
    # Mac dinh ghi vao thu muc rieng cua ma hang dang production (xem
    # gateway/products_registry.yaml) - khong con ghi thang vao gateway/ nua, vi
    # resolve_calib_path() giờ uu tien calib_dir cua ma hang dang active. Dung
    # --output-dir de ghi cho 1 ma hang khac.
    output_dir = Path(args.output_dir) if args.output_dir else (
        project_dir / "gateway" / "products" / "23691025-250616-0004-nvq-aoi"
    )

    if not os.path.exists(excel_path):
        print(f"LỖI: Không tìm thấy file Excel: {excel_path}")
        sys.exit(1)

    print(f"Đọc file Excel: {excel_path}")
    print()

    rows_a, rows_b = read_excel_2mat(excel_path)

    generated = []

    # --- Mặt A ---
    if rows_a:
        print(f"=== MẶT A: {len(rows_a)} điểm ===")
        arr = np.array(rows_a)
        result = fit_bilinear(arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3])
        if result:
            out_path = str(output_dir / "vrs_calib_side_a.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=4, ensure_ascii=False)
            print(f"  -> Đã lưu: {out_path}")
            generated.append(("A", out_path))
        print()
    else:
        print("Mặt A: không có dữ liệu PLC → bỏ qua.\n")

    # --- Mặt B ---
    if rows_b:
        print(f"=== MẶT B: {len(rows_b)} điểm ===")
        arr = np.array(rows_b)
        result = fit_bilinear(arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3])
        if result:
            out_path = str(output_dir / "vrs_calib_side_b.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=4, ensure_ascii=False)
            print(f"  -> Đã lưu: {out_path}")
            generated.append(("B", out_path))
        print()
    else:
        print("Mặt B: không có dữ liệu PLC → bỏ qua.\n")

    # --- Tổng kết ---
    if generated:
        print("=" * 60)
        print("HOÀN TẤT! Đã sinh file calib:")
        for side, path in generated:
            print(f"  Mặt {side}: {path}")
        print("=" * 60)
    else:
        print("Không sinh được file nào. Kiểm tra lại dữ liệu trong Excel.")
        sys.exit(1)


if __name__ == "__main__":
    main()
