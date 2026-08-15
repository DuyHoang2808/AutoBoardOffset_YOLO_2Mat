import json
import os


def load_calibration_matrix(json_path):
    if not os.path.exists(json_path):
        raise FileNotFoundError(
            f"Không tìm thấy file '{json_path}'. Vui lòng chạy script 'Calib_4diem.py' trước!"
        )

    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def board_to_plc(x, y, coeffs):
    """Áp dụng ma trận hệ số thu được từ JSON để tính toán tọa độ PLC.

    Hỗ trợ bilinear (c0..c3) hoặc biquadratic (thêm c4,c5,d4,d5).
    """
    X_PLC = (
        coeffs["c0"]
        + coeffs["c1"] * x
        + coeffs["c2"] * y
        + coeffs["c3"] * x * y
    )
    Y_PLC = (
        coeffs["d0"]
        + coeffs["d1"] * x
        + coeffs["d2"] * y
        + coeffs["d3"] * x * y
    )
    if "c4" in coeffs:
        X_PLC += coeffs["c4"] * x * x + coeffs["c5"] * y * y
        Y_PLC += coeffs["d4"] * x * x + coeffs["d5"] * y * y
    return X_PLC, Y_PLC


if __name__ == "__main__":
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    
    # json_path = os.path.join(BASE_DIR, "vrs_calib_4diem.json")
    # Da doi sang thu muc rieng cua ma hang dang production (xem gateway/products_registry.yaml)
    json_path = r"D:\Camera\Dev\AutoBoardOffset_YOLO_2Mat\gateway\products\23691025-250616-0004-nvq-aoi\vrs_calib_side_b.json"

    # 1. Load các hệ số calib từ file JSON lên bộ nhớ
    try:
        calib_coeffs = load_calibration_matrix(json_path)
        print("--> Đã nạp ma trận hiệu chỉnh thành công từ file JSON.")
    except Exception as e:
        print(f"LỖI: {e}")
        exit()

    print("\n" + "=" * 50)
    print("      CHƯƠNG TRÌNH TRA CỨU TỌA ĐỘ THỰC TẾ PLC")
    print("   (Gõ 'q' hoặc 'exit' tại bất kỳ dòng nào để thoát)")
    print("=" * 50)

    # 2. Vòng lặp cho phép nhập dữ liệu liên tục từ bàn phím
    while True:
        print("\n--- Nhập điểm mới ---")
        try:
            # Nhập tọa độ X
            input_x = input("Nhập tọa độ Board X (mm): ").strip()
            if input_x.lower() in ["q", "exit"]:
                print("Đã thoát chương trình.")
                break

            # Nhập tọa độ Y
            input_y = input("Nhập tọa độ Board Y (mm): ").strip()
            if input_y.lower() in ["q", "exit"]:
                print("Đã thoát chương trình.")
                break

            # Chuyển đổi dữ liệu nhập vào thành số thực (float)
            board_target_x = float(input_x)
            board_target_y = float(input_y)

            # 3. Tính toán ra tọa độ thực tế cho PLC
            plc_x, plc_y = board_to_plc(
                board_target_x, board_target_y, calib_coeffs
            )

            # 4. In kết quả hiển thị trực quan
            print("-" * 50)
            print(
                f" Tọa độ thiết kế (Board): ({board_target_x}, {board_target_y})"
            )
            print(
                f" 👉 GIÁ TRỊ NHẬP PLC  : X = {plc_x:.3f}  |  Y = {plc_y:.3f}"
            )
            print("-" * 50)

        except ValueError:
            print(
                "❌ LỖI: Giá trị nhập vào không hợp lệ! Vui lòng chỉ nhập số thực (ví dụ: 100 hoặc 150.5)."
            )