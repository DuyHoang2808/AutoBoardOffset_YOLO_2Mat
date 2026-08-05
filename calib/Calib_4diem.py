import json
import os

import numpy as np
import pandas as pd


def generate_calibration_matrix(excel_path, json_path):
    print("--> Đang đọc dữ liệu từ file Excel...")

    try:
        # Lần này đọc thô toàn bộ file, không bỏ qua dòng nào để tự động quét tìm tiêu đề
        df_raw = pd.read_excel(excel_path, sheet_name="Calibration_Data", header=None)

        # Vòng lặp quét tìm dòng chứa từ khóa tiêu đề
        header_row_idx = None
        for idx, row in df_raw.iterrows():
            row_str = [str(val) for val in row.values]
            # Nếu dòng nào chứa cả 'Board X' và 'PLC X' thì chính là dòng tiêu đề cần tìm
            if any("Board X" in s for s in row_str) and any("PLC X" in s for s in row_str):
                header_row_idx = idx
                break

        if header_row_idx is None:
            print("LỖI: Không tìm thấy dòng tiêu đề chứa 'Board X' và 'PLC X' ở bất kỳ đâu trong file Excel!")
            print("Vui lòng kiểm tra xem bạn có lỡ tay xóa mất dòng chữ tiêu đề không nhé.")
            return

        # Đã tìm thấy dòng tiêu đề, xác định tên cột
        headers = [str(c).strip() for c in df_raw.iloc[header_row_idx].values]
        col_board_x = [c for c in headers if "Board X" in c][0]
        col_board_y = [c for c in headers if "Board Y" in c][0]
        col_plc_x = [c for c in headers if "PLC X" in c][0]
        col_plc_y = [c for c in headers if "PLC Y" in c][0]

        # Tự động quét số dòng dữ liệu thực tế bên dưới tiêu đề (>= 4 điểm).
        # Cho phép đo DƯ điểm (5, 6, ...) để tăng độ bền vững bằng bình phương
        # tối thiểu, thay vì luôn cố định đúng 4 điểm như trước. Dừng quét khi
        # gặp dòng trống hoàn toàn (hết dữ liệu).
        max_scan = min(200, len(df_raw) - header_row_idx - 1)
        data_rows = []
        for offset in range(1, max_scan + 1):
            row = df_raw.iloc[header_row_idx + offset]
            row_series = pd.Series(row.values, index=headers)
            bx = pd.to_numeric(pd.Series([row_series[col_board_x]]), errors="coerce").iloc[0]
            by = pd.to_numeric(pd.Series([row_series[col_board_y]]), errors="coerce").iloc[0]
            px = pd.to_numeric(pd.Series([row_series[col_plc_x]]), errors="coerce").iloc[0]
            py = pd.to_numeric(pd.Series([row_series[col_plc_y]]), errors="coerce").iloc[0]

            if np.isnan(bx) and np.isnan(by) and np.isnan(px) and np.isnan(py):
                break  # dòng trống -> hết dữ liệu

            data_rows.append((bx, by, px, py))

        if len(data_rows) < 4:
            print(
                f"LỖI: Chỉ tìm thấy {len(data_rows)} điểm dữ liệu hợp lệ. "
                "Cần tối thiểu 4 điểm để giải mô hình bilinear (4 tham số/trục)."
            )
            return

        board_x = np.array([r[0] for r in data_rows], dtype=float)
        board_y = np.array([r[1] for r in data_rows], dtype=float)
        plc_x = np.array([r[2] for r in data_rows], dtype=float)
        plc_y = np.array([r[3] for r in data_rows], dtype=float)

    except Exception as e:
        print(f"LỖI hệ thống khi đọc file Excel. Chi tiết: {e}")
        return

    n_points = len(board_x)
    print(f"--> Tìm thấy {n_points} điểm dữ liệu hợp lệ.")

    # Kiểm tra dữ liệu trống hoặc lỗi định dạng chữ (đã lọc NaN ở bước quét,
    # nhưng vẫn kiểm tra lại cho chắc chắn)
    if (
        np.isnan(board_x).any()
        or np.isnan(board_y).any()
        or np.isnan(plc_x).any()
        or np.isnan(plc_y).any()
    ):
        print("LỖI: Có ô dữ liệu bị TRỐNG hoặc chứa KÝ TỰ CHỮ trong các dòng số liệu của bạn.")
        print("Hãy chắc chắn rằng bên dưới dòng tiêu đề là các dòng chứa con số hợp lệ, không xen kẽ dòng trống.")
        return

    # Xây dựng ma trận hệ phương trình Bilinear (N điểm, 4 tham số mỗi trục)
    A = np.column_stack([np.ones(n_points), board_x, board_y, board_x * board_y])

    # --- Kiểm tra độ suy biến / độ nhạy của phép giải (Condition Number) ---
    # Ma trận A gốc có các cột lệch nhau rất nhiều về độ lớn (1 so với x*y có thể
    # lên tới hàng trăm nghìn) nên condition number "thô" luôn rất lớn một cách
    # tự nhiên, không phản ánh đúng chất lượng phân bố điểm. Vì vậy ta chuẩn hóa
    # tọa độ về khoảng [0,1] chỉ để tính condition number chẩn đoán (không dùng
    # để giải hệ số thực tế, hệ số cuối cùng vẫn theo đúng đơn vị mm gốc).
    scale = max(np.abs(board_x).max(), np.abs(board_y).max(), 1e-9)
    xn, yn = board_x / scale, board_y / scale
    A_diag = np.column_stack([np.ones(n_points), xn, yn, xn * yn])
    cond_number = np.linalg.cond(A_diag)

    print(f"--> Condition number (đã chuẩn hóa) của ma trận điểm calib: {cond_number:.2f}")
    if cond_number > 1000:
        print(
            "    CẢNH BÁO NGHIÊM TRỌNG: Các điểm calib gần như thẳng hàng hoặc trùng nhau. "
            "Hệ số tính ra sẽ RẤT nhạy với sai số đo, nên chọn lại vị trí điểm phân bố đều hơn "
            "(ưu tiên trải rộng ra các góc/biên, tránh gần thẳng hàng)."
        )
    elif cond_number > 100:
        print(
            "    Lưu ý: các điểm calib chưa phân bố lý tưởng (hơi thẳng hàng hoặc lệch về 1 phía). "
            "Kết quả vẫn dùng được nhưng nên cân nhắc chọn điểm trải đều hơn nếu có thể."
        )
    else:
        print("    Phân bố điểm calib tốt, hệ số tính ra ổn định.")

    # Giải hệ phương trình bằng bình phương tối thiểu (Least Squares).
    # Với đúng 4 điểm, kết quả tương đương np.linalg.solve (hệ xác định đúng).
    # Với >4 điểm (đo dư), kết quả là nghiệm tối ưu bình phương tối thiểu,
    # giúp trung bình hoá và giảm ảnh hưởng của 1 điểm đo lỗi ngẫu nhiên.
    try:
        c_coeffs, _, rank, _ = np.linalg.lstsq(A, plc_x, rcond=None)
        d_coeffs, _, _, _ = np.linalg.lstsq(A, plc_y, rcond=None)
    except np.linalg.LinAlgError:
        print("LỖI: Ma trận tọa độ không hợp lệ (các điểm bị trùng hoặc thẳng hàng).")
        return

    if rank < 4:
        print(
            "LỖI: Ma trận thiếu hạng (rank "
            f"{rank}/4) - các điểm calib bị suy biến hoàn toàn (trùng hoặc thẳng hàng tuyệt đối). "
            "Không thể giải ra 4 tham số độc lập. Vui lòng đo lại điểm khác vị trí."
        )
        return

    # --- Báo cáo sai số dư (residual) từng điểm để phát hiện điểm đo lỗi (outlier) ---
    pred_x = A @ c_coeffs
    pred_y = A @ d_coeffs
    residuals = np.sqrt((pred_x - plc_x) ** 2 + (pred_y - plc_y) ** 2)
    rms_error = float(np.sqrt(np.mean(residuals**2)))
    max_error = float(np.max(residuals))

    print(f"--> Sai số dư (residual) sau khi fit: RMS = {rms_error:.4f} mm, Max = {max_error:.4f} mm")
    if n_points == 4:
        print("    (Đúng 4 điểm => hệ phương trình xác định đúng, residual gần như bằng 0.)")
    for i, res in enumerate(residuals):
        flag = ""
        if n_points > 4 and res > 2 * rms_error and res > 0.02:
            flag = "  <-- NGHI NGỜ điểm đo lỗi (outlier), nên kiểm tra/đo lại điểm này!"
        print(f"    Điểm {i + 1} (Board {board_x[i]:.3f}, {board_y[i]:.3f}): residual = {res:.4f} mm{flag}")

    # Tổ chức dữ liệu lưu trữ hằng số ma trận calib
    matrix_data = {
        "c0": float(c_coeffs[0]),
        "c1": float(c_coeffs[1]),
        "c2": float(c_coeffs[2]),
        "c3": float(c_coeffs[3]),
        "d0": float(d_coeffs[0]),
        "d1": float(d_coeffs[1]),
        "d2": float(d_coeffs[2]),
        "d3": float(d_coeffs[3]),
        "_diagnostics": {
            "n_points": n_points,
            "condition_number": cond_number,
            "rms_residual_mm": rms_error,
            "max_residual_mm": max_error,
        },
    }

    # Ghi dữ liệu ra file JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(matrix_data, f, indent=4, ensure_ascii=False)

    print(f" ĐÃ TÍNH TOÁN XONG THÀNH CÔNG! Ma trận đã lưu vào: '{json_path}'")


if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    excel_path = os.path.join(BASE_DIR, "calib_6diem.xlsx")
    json_path = os.path.join(BASE_DIR, "vrs_calib_4diem.json")

    generate_calibration_matrix(excel_path, json_path)
