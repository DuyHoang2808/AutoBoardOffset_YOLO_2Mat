"""
calib_2mat.py — Sinh ma trận calib tĩnh cho board 2 mặt (A/B) từ 1 file Excel.

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
# Core: fit calib model (tự chọn bilinear 4-param hoặc biquadratic 6-param)
# ==============================================================================

# Ngưỡng RMS để tự động nâng bậc: nếu bilinear RMS > threshold VÀ có ≥6 điểm
# → thử biquadratic.
_AUTO_UPGRADE_RMS_THRESHOLD_MM = 1.0


def _build_design_matrix(board_x, board_y, order: str):
    """Tạo design matrix cho bilinear hoặc biquadratic."""
    n = len(board_x)
    ones = np.ones(n)
    if order == "biquadratic":
        # c0 + c1*x + c2*y + c3*x*y + c4*x² + c5*y²
        return np.column_stack([ones, board_x, board_y,
                                board_x * board_y,
                                board_x ** 2, board_y ** 2])
    else:
        # c0 + c1*x + c2*y + c3*x*y
        return np.column_stack([ones, board_x, board_y,
                                board_x * board_y])


def _fit_one_order(board_x, board_y, plc_x, plc_y, order: str):
    """Fit 1 bậc, trả (c_coeffs, d_coeffs, cond_number, rank) hoặc None."""
    A = _build_design_matrix(board_x, board_y, order)
    n_params = A.shape[1]

    # Condition number trên toạ độ chuẩn hoá
    scale = max(np.abs(board_x).max(), np.abs(board_y).max(), 1e-9)
    xn, yn = board_x / scale, board_y / scale
    A_norm = _build_design_matrix(xn, yn, order)
    cond_number = float(np.linalg.cond(A_norm))

    try:
        c_coeffs, _, rank, _ = np.linalg.lstsq(A, plc_x, rcond=None)
        d_coeffs, _, _, _ = np.linalg.lstsq(A, plc_y, rcond=None)
    except np.linalg.LinAlgError:
        return None

    if rank < n_params:
        return None

    return c_coeffs, d_coeffs, cond_number, rank


def fit_calib_model(board_x: np.ndarray, board_y: np.ndarray,
                    plc_x: np.ndarray, plc_y: np.ndarray,
                    point_names: list[str] | None = None,
                    force_order: str | None = None) -> dict | None:
    """Fit mô hình calib Board→PLC.

    - 4-5 điểm: bilinear (c0..c3).
    - ≥6 điểm: thử bilinear trước; nếu RMS > 1mm → tự nâng lên biquadratic
      (c0..c5 bao gồm x², y²).
    - force_order: "bilinear" hoặc "biquadratic" để ép bậc.

    Trả dict hệ số + _diagnostics, hoặc None nếu không fit được.
    """
    n = len(board_x)
    if n < 4:
        print(f"  LỖI: Chỉ có {n} điểm, cần tối thiểu 4.")
        return None

    names = point_names or [str(i + 1) for i in range(n)]

    # Quyết định bậc
    if force_order:
        order = force_order
    elif n >= 6:
        # Thử bilinear trước, nếu RMS cao → nâng bậc
        order = "bilinear"  # sẽ kiểm tra sau
    else:
        order = "bilinear"

    result = _fit_one_order(board_x, board_y, plc_x, plc_y, order)
    if result is None:
        print("  LỖI: Ma trận không hợp lệ (điểm trùng/thẳng hàng).")
        return None

    c_coeffs, d_coeffs, cond_number, rank = result
    A = _build_design_matrix(board_x, board_y, order)
    residuals = np.sqrt((A @ c_coeffs - plc_x) ** 2 + (A @ d_coeffs - plc_y) ** 2)
    rms_error = float(np.sqrt(np.mean(residuals ** 2)))

    # Auto-upgrade: bilinear RMS quá cao + đủ điểm → thử biquadratic
    if (order == "bilinear" and not force_order
            and n >= 6 and rms_error > _AUTO_UPGRADE_RMS_THRESHOLD_MM):
        print(f"  Bilinear RMS = {rms_error:.2f} mm > {_AUTO_UPGRADE_RMS_THRESHOLD_MM} mm"
              f" — thử nâng lên biquadratic (6 params)...")
        result2 = _fit_one_order(board_x, board_y, plc_x, plc_y, "biquadratic")
        if result2 is not None:
            c2, d2, cond2, rank2 = result2
            A2 = _build_design_matrix(board_x, board_y, "biquadratic")
            res2 = np.sqrt((A2 @ c2 - plc_x) ** 2 + (A2 @ d2 - plc_y) ** 2)
            rms2 = float(np.sqrt(np.mean(res2 ** 2)))
            if rms2 < rms_error:
                print(f"  Biquadratic RMS = {rms2:.4f} mm — tốt hơn, dùng biquadratic.")
                order = "biquadratic"
                c_coeffs, d_coeffs, cond_number = c2, d2, cond2
                A, residuals, rms_error = A2, res2, rms2
            else:
                print(f"  Biquadratic không cải thiện — giữ bilinear.")

    # Report
    n_params = len(c_coeffs)
    model_label = "biquadratic" if order == "biquadratic" else "bilinear"
    max_error = float(np.max(residuals))

    if cond_number > 1e8:
        print(f"  CẢNH BÁO: Condition number = {cond_number:.1f} — cao (biquadratic), nhưng float64 đủ chính xác.")
    elif cond_number > 1000:
        print(f"  CẢNH BÁO: Condition number = {cond_number:.1f} — điểm gần thẳng hàng!")
    elif cond_number > 100:
        print(f"  Lưu ý: Condition number = {cond_number:.1f} — điểm chưa phân bố lý tưởng.")
    else:
        print(f"  Condition number = {cond_number:.1f} — OK.")

    print(f"  Model: {model_label} ({n_params} params)")
    print(f"  Residual: RMS = {rms_error:.4f} mm, Max = {max_error:.4f} mm")
    for i, res in enumerate(residuals):
        flag = ""
        if n > n_params and res > 2 * rms_error and res > 0.02:
            flag = "  <-- NGHI NGỜ outlier!"
        print(f"    Điểm {names[i]} (Board {board_x[i]:.3f}, {board_y[i]:.3f}): {res:.4f} mm{flag}")

    # Build output dict
    out = {
        "c0": float(c_coeffs[0]), "c1": float(c_coeffs[1]),
        "c2": float(c_coeffs[2]), "c3": float(c_coeffs[3]),
        "d0": float(d_coeffs[0]), "d1": float(d_coeffs[1]),
        "d2": float(d_coeffs[2]), "d3": float(d_coeffs[3]),
    }
    if order == "biquadratic":
        out["c4"] = float(c_coeffs[4])
        out["c5"] = float(c_coeffs[5])
        out["d4"] = float(d_coeffs[4])
        out["d5"] = float(d_coeffs[5])

    out["_diagnostics"] = {
        "n_points": n,
        "point_names_used": names,
        "model": model_label,
        "n_params": n_params,
        "condition_number": cond_number,
        "rms_residual_mm": rms_error,
        "max_residual_mm": max_error,
    }
    return out


# Backward-compatible alias
def fit_bilinear(board_x, board_y, plc_x, plc_y, point_names=None):
    return fit_calib_model(board_x, board_y, plc_x, plc_y,
                           point_names=point_names, force_order="bilinear")


# ==============================================================================
# Đọc Excel 2 mặt
# ==============================================================================

def read_excel_2mat(excel_path: str) -> tuple[list, list]:
    """Đọc Excel, trả (rows_side_a, rows_side_b).

    Mỗi row = (name, board_x, board_y, plc_x, plc_y). Chỉ trả row có đủ 4 số.
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

    idx_name = 0  # cột đầu tiên là tên điểm
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

        name = str(row[idx_name]).strip() if not pd.isna(row[idx_name]) else f"P{offset}"
        bx, by = to_num(idx_bx), to_num(idx_by)
        if np.isnan(bx) and np.isnan(by):
            break  # hết dữ liệu

        pxa, pya = to_num(idx_pxa), to_num(idx_pya)
        if not (np.isnan(pxa) or np.isnan(pya)):
            rows_a.append((name, bx, by, pxa, pya))

        pxb, pyb = to_num(idx_pxb), to_num(idx_pyb)
        if not (np.isnan(pxb) or np.isnan(pyb)):
            rows_b.append((name, bx, by, pxb, pyb))

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
    parser.add_argument("--exclude-points", default=None,
                        help="Loại bỏ điểm theo tên, phân cách bởi dấu phẩy (vd: E,F). "
                             "Áp dụng cho CẢ 2 mặt. Dùng --exclude-points-a hoặc --exclude-points-b "
                             "nếu chỉ muốn loại cho 1 mặt.")
    parser.add_argument("--exclude-points-a", default=None,
                        help="Loại bỏ điểm chỉ cho mặt A (vd: E,F)")
    parser.add_argument("--exclude-points-b", default=None,
                        help="Loại bỏ điểm chỉ cho mặt B (vd: E,F)")
    args = parser.parse_args()

    # Parse exclude sets
    def parse_exclude(arg_val):
        if not arg_val:
            return set()
        return {s.strip().upper() for s in arg_val.split(",") if s.strip()}

    exclude_all = parse_exclude(args.exclude_points)
    exclude_a = parse_exclude(args.exclude_points_a) | exclude_all
    exclude_b = parse_exclude(args.exclude_points_b) | exclude_all

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

    def process_side(rows, side_label, exclude_set):
        """Lọc điểm, fit model (tự chọn bilinear/biquadratic), lưu JSON."""
        if not rows:
            print(f"Mặt {side_label}: không có dữ liệu PLC → bỏ qua.\n")
            return None

        # Lọc điểm bị exclude
        if exclude_set:
            before = len(rows)
            rows = [r for r in rows if r[0].upper() not in exclude_set]
            if before != len(rows):
                print(f"  (Đã loại {before - len(rows)} điểm: {', '.join(sorted(exclude_set))})")

        if len(rows) < 4:
            print(f"Mặt {side_label}: chỉ còn {len(rows)} điểm sau khi loại → cần >= 4.\n")
            return None

        names = [r[0] for r in rows]
        arr = np.array([(r[1], r[2], r[3], r[4]) for r in rows], dtype=float)
        print(f"=== MẶT {side_label}: {len(rows)} điểm ({', '.join(names)}) ===")

        result = fit_calib_model(arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3],
                                 point_names=names)
        if result:
            fname = f"vrs_calib_side_{side_label.lower()}.json"
            out_path = str(output_dir / fname)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=4, ensure_ascii=False)
            print(f"  -> Đã lưu: {out_path}")
            print()
            return (side_label, out_path)
        print()
        return None

    # --- Mặt A ---
    res_a = process_side(rows_a, "A", exclude_a)
    if res_a:
        generated.append(res_a)

    # --- Mặt B ---
    res_b = process_side(rows_b, "B", exclude_b)
    if res_b:
        generated.append(res_b)

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
