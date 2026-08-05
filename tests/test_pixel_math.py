"""
Unit test cho pixel_offset_to_plc_mm() trong gateway/plc_offset_gateway.py.
KHÔNG cần phần cứng (PLC/camera) - chỉ kiểm tra toán học của phép quy đổi
lệch pixel -> lệch mm theo trục PLC (đảo ngược ma trận T).
"""

import math

import numpy as np
import pytest

from plc_offset_gateway import measured_plc_from_marker, pixel_offset_to_plc_mm


def test_identity_scale_no_rotation():
    """T = diag(10,10): 10 px / 1mm cả 2 trục, không xoay/lật."""
    T = [[10.0, 0.0], [0.0, 10.0]]
    dx_mm, dy_mm = pixel_offset_to_plc_mm(50.0, -30.0, T)
    assert dx_mm == pytest.approx(5.0)
    assert dy_mm == pytest.approx(-3.0)


def test_axis_flip():
    """Trục X ảnh tương ứng trục -X máy (camera lắp lật)."""
    T = [[-10.0, 0.0], [0.0, 10.0]]
    dx_mm, dy_mm = pixel_offset_to_plc_mm(20.0, 20.0, T)
    assert dx_mm == pytest.approx(-2.0)
    assert dy_mm == pytest.approx(2.0)


def test_roundtrip_with_rotation_and_scale():
    """Round-trip: tạo T từ 1 góc xoay + tỉ lệ bất kỳ, forward-map 1 delta PLC ra pixel,
    sau đó pixel_offset_to_plc_mm() phải cho lại đúng delta PLC ban đầu."""
    theta = math.radians(7.3)  # mô phỏng camera lắp lệch nhẹ 7.3 độ so với trục máy
    scale = 3.162  # px/mm xấp xỉ theo FOV đề bài (1080/341.5)
    c, s = math.cos(theta), math.sin(theta)
    T = np.array([[scale * c, -scale * s], [scale * s, scale * c]])

    for dx_plc, dy_plc in [(1.0, 0.0), (0.0, 1.0), (2.5, -3.7), (-4.0, -4.0)]:
        pixel_shift = T @ np.array([dx_plc, dy_plc])
        dx_mm, dy_mm = pixel_offset_to_plc_mm(pixel_shift[0], pixel_shift[1], T.tolist())
        assert dx_mm == pytest.approx(dx_plc, abs=1e-9)
        assert dy_mm == pytest.approx(dy_plc, abs=1e-9)


def test_singular_matrix_raises():
    T = [[1.0, 2.0], [2.0, 4.0]]  # hàng 2 = 2x hàng 1 -> det = 0
    with pytest.raises(ValueError):
        pixel_offset_to_plc_mm(10.0, 10.0, T)


def test_measured_plc_sign_convention_roundtrip():
    """KHOÁ DẤU: khi camera ở `expected`, marker thực sự nằm cách đó true_disp (mm PLC).
    Theo định nghĩa T (pixel_shift = T·plc_shift), marker xuất hiện ở pixel:
        pixel = center - T·true_disp
    measured_plc_from_marker phải khôi phục lại đúng expected + true_disp
    (KHÔNG phải expected - true_disp - đó là bug cũ)."""
    image_w, image_h = 1920, 1080
    # T mô phỏng camera zoom sâu + trục X bị lật (giống dữ liệu thực tế của người dùng)
    T = np.array([[-182.8, 0.0], [0.0, 182.8]])
    expected = (97.027, 474.158)

    for true_disp in [(1.4995, 0.0816), (-2.3, 0.5), (0.0, -1.1)]:
        off = -(T @ np.array(true_disp))  # pixel offset marker so với tâm
        marker_px = image_w / 2.0 + off[0]
        marker_py = image_h / 2.0 + off[1]

        mx, my = measured_plc_from_marker(*expected, marker_px, marker_py, image_w, image_h, T.tolist())

        assert mx == pytest.approx(expected[0] + true_disp[0], abs=1e-6)
        assert my == pytest.approx(expected[1] + true_disp[1], abs=1e-6)


def test_measured_plc_invert_flags():
    """Cờ camera_axis_invert_x/y đảo dấu độ lệch mm của trục tương ứng."""
    image_w, image_h = 1920, 1080
    T = [[100.0, 0.0], [0.0, 100.0]]  # 100 px/mm, đường chéo dương, không xoay
    expected = (50.0, 60.0)
    marker_px = image_w / 2.0 + 200  # lệch 200px sang phải
    marker_py = image_h / 2.0 + 100  # lệch 100px xuống dưới -> T^-1: (2.0mm, 1.0mm)

    mx, my = measured_plc_from_marker(*expected, marker_px, marker_py, image_w, image_h, T)
    assert (mx, my) == pytest.approx((48.0, 59.0))  # expected - (2,1)

    mx, my = measured_plc_from_marker(*expected, marker_px, marker_py, image_w, image_h, T, invert_x=True)
    assert (mx, my) == pytest.approx((52.0, 59.0))  # dx đảo dấu

    mx, my = measured_plc_from_marker(
        *expected, marker_px, marker_py, image_w, image_h, T, invert_x=True, invert_y=True
    )
    assert (mx, my) == pytest.approx((52.0, 61.0))  # cả hai đảo dấu


def test_pixel_offset_inverts_default_matrix_roundtrip():
    """pixel_offset_to_plc_mm phải là NGHỊCH ĐẢO đúng của ma trận T mặc định trong model,
    bất kể giá trị cụ thể (không hardcode FOV - để không vỡ khi người dùng đổi zoom/FOV)."""
    from plc_offset_gateway import GatewayConfigModel

    T = GatewayConfigModel().camera_axis_matrix
    for plc_x, plc_y in [(1.0, 0.0), (0.0, 1.0), (2.3, -1.1)]:
        # map plc_shift -> pixel_shift bằng T, rồi hoàn nguyên phải ra đúng plc_shift ban đầu
        dpx = T[0][0] * plc_x + T[0][1] * plc_y
        dpy = T[1][0] * plc_x + T[1][1] * plc_y
        rx, ry = pixel_offset_to_plc_mm(dpx, dpy, T)
        assert rx == pytest.approx(plc_x, abs=1e-9)
        assert ry == pytest.approx(plc_y, abs=1e-9)
