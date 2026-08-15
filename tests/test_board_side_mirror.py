"""
Unit test cho logic resolver calib/offset theo mat board (A/B) + validate_board_side,
va theo ma hang dang active (products_registry.yaml). KHONG can phan cung - chi test
ham thuan resolve_calib_path(), resolve_offset_path(), validate_board_side() trong
gateway/plc_offset_gateway.py.

Thay the hoan toan test lat guong cu (mirror_board_xy_for_side da bi xoa - PA2) VA test
co che fallback calib_paths/vrs_calib_json_path cu (da bi xoa khi chuyen sang mo hinh
nhieu ma hang - moi mat board gio LUON resolve theo calib_dir cua ma hang dang active).
"""

import json
import os
import pytest
from fastapi import HTTPException

import plc_offset_gateway as gw


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def _restore_product_state():
    """Sao luu / khoi phuc PRODUCTS_REGISTRY + ACTIVE_PRODUCT_CODE, tranh anh huong test khac."""
    saved_registry = gw.PRODUCTS_REGISTRY
    saved_active = gw.ACTIVE_PRODUCT_CODE
    yield
    gw.PRODUCTS_REGISTRY = saved_registry
    gw.ACTIVE_PRODUCT_CODE = saved_active


@pytest.fixture
def active_product(tmp_path, _restore_product_state):
    """Dat 1 ma hang gia lam active, calib_dir tro vao tmp_path, tao san file calib gia (A/B)."""
    calib_a = tmp_path / "vrs_calib_side_a.json"
    calib_a.write_text(json.dumps({
        "coefficients": {"ax": 1, "bx": 0, "cx": 0, "ay": 0, "by": 1, "cy": 0}
    }))

    # Calib B - calib gia (determinant co the nguoc dau)
    calib_b = tmp_path / "vrs_calib_side_b.json"
    calib_b.write_text(json.dumps({
        "coefficients": {"ax": -1.0, "bx": 0.0, "cx": 356.386, "ay": 0.0, "by": 1.0, "cy": 0.0}
    }))

    gw.PRODUCTS_REGISTRY = {
        "default_product": "TEST_PRODUCT",
        "products": {
            "TEST_PRODUCT": {
                "weights_path": "unused.pt",
                "class_name_filter": None,
                "imgsz": 640,
                "calib_dir": str(tmp_path),
            },
        },
    }
    gw.ACTIVE_PRODUCT_CODE = "TEST_PRODUCT"

    return {
        "calib_a": str(calib_a),
        "calib_b": str(calib_b),
        "offset_a": str(tmp_path / "offset_runtime_side_a.json"),
        "offset_b": str(tmp_path / "offset_runtime_side_b.json"),
    }


@pytest.fixture
def no_active_product(_restore_product_state):
    """Khong co ma hang nao active (products_registry.yaml rong/loi)."""
    gw.PRODUCTS_REGISTRY = {"default_product": None, "products": {}}
    gw.ACTIVE_PRODUCT_CODE = None


# ==============================================================================
# Tests: resolve_calib_path
# ==============================================================================

def test_resolve_calib_path_side_a(active_product):
    assert gw.resolve_calib_path("A") == active_product["calib_a"]


def test_resolve_calib_path_side_b(active_product):
    assert gw.resolve_calib_path("B") == active_product["calib_b"]


def test_resolve_calib_path_case_insensitive(active_product):
    """board_side "a" va "b" (lowercase) van hoat dong."""
    assert gw.resolve_calib_path("a") == active_product["calib_a"]
    assert gw.resolve_calib_path("b") == active_product["calib_b"]


def test_resolve_calib_path_no_active_product_raises(no_active_product):
    """Khong co ma hang active -> HTTPException 400 ro rang, khong con fallback ngam."""
    with pytest.raises(HTTPException) as exc:
        gw.resolve_calib_path("A")
    assert exc.value.status_code == 400
    assert "mã hàng" in exc.value.detail


# ==============================================================================
# Tests: resolve_offset_path
# ==============================================================================

def test_resolve_offset_path_side_a(active_product):
    assert gw.resolve_offset_path("A") == active_product["offset_a"]


def test_resolve_offset_path_side_b(active_product):
    assert gw.resolve_offset_path("B") == active_product["offset_b"]


def test_resolve_offset_path_no_active_product_raises(no_active_product):
    with pytest.raises(HTTPException) as exc:
        gw.resolve_offset_path("A")
    assert exc.value.status_code == 400


# ==============================================================================
# Tests: validate_board_side
# ==============================================================================

def test_validate_board_side_a_ok(active_product):
    """Mat A voi file calib ton tai -> khong raise."""
    gw.validate_board_side("A")  # khong duoc raise


def test_validate_board_side_b_ok(active_product):
    """Mat B voi file calib ton tai -> khong raise."""
    gw.validate_board_side("B")


def test_validate_board_side_invalid_value_raises(active_product):
    with pytest.raises(HTTPException) as exc:
        gw.validate_board_side("C")
    assert exc.value.status_code == 400


def test_validate_board_side_file_not_exists_raises(tmp_path, _restore_product_state):
    """Mat B co ma hang active nhung chua co file calib trong calib_dir -> raise."""
    gw.PRODUCTS_REGISTRY = {
        "default_product": "EMPTY_PRODUCT",
        "products": {
            "EMPTY_PRODUCT": {
                "weights_path": "unused.pt", "class_name_filter": None, "imgsz": 640,
                "calib_dir": str(tmp_path),  # thu muc rong, chua co file calib nao
            },
        },
    }
    gw.ACTIVE_PRODUCT_CODE = "EMPTY_PRODUCT"

    with pytest.raises(HTTPException) as exc:
        gw.validate_board_side("B")
    assert "không tồn tại" in exc.value.detail


def test_validate_board_side_no_active_product_raises(no_active_product):
    """Khong co ma hang active -> validate_board_side cung raise (qua resolve_calib_path)."""
    with pytest.raises(HTTPException) as exc:
        gw.validate_board_side("A")
    assert exc.value.status_code == 400


# ==============================================================================
# Tests: dam bao khong con mirror (PA2)
# ==============================================================================

def test_no_mirror_function_exists():
    """Xac nhan mirror_board_xy_for_side DA BI XOA khoi module."""
    assert not hasattr(gw, "mirror_board_xy_for_side"), \
        "mirror_board_xy_for_side van con trong module - PA2 yeu cau xoa!"


def test_no_mirror_config_keys():
    """Xac nhan config model KHONG con board_side_b_mirror_axis / board_side_b_mirror_reference_mm."""
    model_fields = set(gw.GatewayConfigModel.model_fields.keys())
    assert "board_side_b_mirror_axis" not in model_fields
    assert "board_side_b_mirror_reference_mm" not in model_fields


# ==============================================================================
# Tests: dam bao khong con field calib_paths/offset_paths cu (thay bang products_registry.yaml)
# ==============================================================================

def test_legacy_calib_fields_removed():
    """Xac nhan calib_paths/offset_paths/vrs_calib_json_path/offset_runtime_json_path (fallback
    PA2 cu, 1-ma-hang-duy-nhat) DA BI XOA khoi GatewayConfigModel - thay bang
    products_registry.yaml (nhieu ma hang, xem get_active_product/resolve_calib_path)."""
    model_fields = set(gw.GatewayConfigModel.model_fields.keys())
    assert "calib_paths" not in model_fields
    assert "offset_paths" not in model_fields
    assert "vrs_calib_json_path" not in model_fields
    assert "offset_runtime_json_path" not in model_fields
