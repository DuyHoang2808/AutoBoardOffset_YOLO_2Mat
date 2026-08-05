"""
Test hồi quy (regression) cho các hàm THUẦN import từ bu_lech_board.py, đảm bảo việc copy
độc lập sang AutoBoardOffset_YOLO_2Mat không làm sai lệch LOGIC gốc.

LƯU Ý: các test này KHÔNG so với file vrs_calib_4diem.json/anchor pool thật (những thứ đó
người dùng có thể chỉnh theo board/máy thực tế), mà kiểm tra CÔNG THỨC bằng hằng số nội
tại - nên không bị "vỡ" khi file dữ liệu thật thay đổi.
"""

import math

import pytest

from bu_lech_board import board_to_plc, kabsch_2d

# Bộ hệ số bilinear cố định (không phụ thuộc file thật) để khoá công thức board_to_plc.
FIXED_COEFFS = {
    "c0": 1.0, "c1": 2.0, "c2": 0.5, "c3": 0.001,
    "d0": -1.0, "d1": 0.25, "d2": 3.0, "d3": -0.002,
}

# Số liệu THẬT lấy từ offset_runtime.json (đo tay, chế độ 2 điểm A/C) của dự án gốc -
# dùng làm golden test cho kabsch_2d (thuật toán thuần, không đọc file nào).
REAL_EXPECTED_PLC = [
    [7.337141392679389, 13.769373455357885],
    [345.4480227091908, 568.6654276937751],
]
REAL_MEASURED_PLC = [
    [8.007, 27.252],
    [369.346, 567.4023],
]
REAL_THETA_DEG = -2.4260343714777934
REAL_TX = 0.11489209375307041
REAL_TY = 13.837401642472685
REAL_RMS_MM = 0.03832560452619691


def test_board_to_plc_bilinear_formula():
    """Khoá công thức bilinear: X = c0 + c1*x + c2*y + c3*x*y (tương tự cho Y)."""
    x, y = 10.0, 5.0
    px, py = board_to_plc(x, y, FIXED_COEFFS)
    exp_x = 1.0 + 2.0 * x + 0.5 * y + 0.001 * x * y
    exp_y = -1.0 + 0.25 * x + 3.0 * y - 0.002 * x * y
    assert px == pytest.approx(exp_x, abs=1e-9)
    assert py == pytest.approx(exp_y, abs=1e-9)


def test_kabsch_matches_real_offset_runtime_result():
    """kabsch_2d() với đúng input thật phải cho lại đúng theta/tx/ty/rms đã lưu trước đây."""
    R, t, theta, rms_error, max_error, residuals = kabsch_2d(REAL_EXPECTED_PLC, REAL_MEASURED_PLC)

    assert math.degrees(theta) == pytest.approx(REAL_THETA_DEG, abs=1e-6)
    assert t[0] == pytest.approx(REAL_TX, abs=1e-6)
    assert t[1] == pytest.approx(REAL_TY, abs=1e-6)
    assert rms_error == pytest.approx(REAL_RMS_MM, abs=1e-6)
