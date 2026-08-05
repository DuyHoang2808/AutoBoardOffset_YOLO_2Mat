"""
Unit test cho logic PA2: resolver calib/offset theo mat board (A/B) + validate_board_side.
KHONG can phan cung - chi test ham thuan resolve_calib_path(), resolve_offset_path(),
validate_board_side() trong gateway/plc_offset_gateway.py.

Thay the hoan toan test lat guong cu (mirror_board_xy_for_side da bi xoa trong PA2).
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
def cfg_backup():
    """Sao luu / khoi phuc cac khoa config bi test sua, tranh anh huong test khac."""
    keys = [
        "calib_paths", "offset_paths",
        "vrs_calib_json_path", "offset_runtime_json_path",
    ]
    saved = {k: gw.GATEWAY_CONFIG.get(k) for k in keys}
    yield
    for k, v in saved.items():
        gw.GATEWAY_CONFIG[k] = v


@pytest.fixture
def tmp_calib_files(tmp_path, cfg_backup):
    """Tao file calib gia cho ca mat A va B trong tmp_path."""
    # Calib A - copy tu vrs_calib_4diem.json that
    calib_a = tmp_path / "vrs_calib_side_a.json"
    real_calib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gateway", "vrs_calib_4diem.json")
    if os.path.exists(real_calib):
        import shutil
        shutil.copy2(real_calib, str(calib_a))
    else:
        # Fallback: tao calib gia don gian
        calib_a.write_text(json.dumps({
            "coefficients": {"ax": 1, "bx": 0, "cx": 0, "ay": 0, "by": 1, "cy": 0}
        }))

    # Calib B - calib gia (determinant co the nguoc dau)
    calib_b = tmp_path / "vrs_calib_side_b.json"
    calib_b.write_text(json.dumps({
        "coefficients": {"ax": -1.0, "bx": 0.0, "cx": 356.386, "ay": 0.0, "by": 1.0, "cy": 0.0}
    }))

    # Offset A
    offset_a = tmp_path / "offset_runtime_side_a.json"
    # Offset B
    offset_b = tmp_path / "offset_runtime_side_b.json"

    gw.GATEWAY_CONFIG["calib_paths"] = {
        "A": str(calib_a),
        "B": str(calib_b),
    }
    gw.GATEWAY_CONFIG["offset_paths"] = {
        "A": str(offset_a),
        "B": str(offset_b),
    }
    gw.GATEWAY_CONFIG["vrs_calib_json_path"] = str(calib_a)
    gw.GATEWAY_CONFIG["offset_runtime_json_path"] = str(offset_a)

    return {
        "calib_a": str(calib_a),
        "calib_b": str(calib_b),
        "offset_a": str(offset_a),
        "offset_b": str(offset_b),
    }


# ==============================================================================
# Tests: resolve_calib_path
# ==============================================================================

def test_resolve_calib_path_side_a_from_map(tmp_calib_files):
    """Mat A: lay tu calib_paths["A"]."""
    path = gw.resolve_calib_path("A")
    assert path == tmp_calib_files["calib_a"]


def test_resolve_calib_path_side_a_fallback(cfg_backup):
    """Mat A: neu calib_paths khong co A -> fallback ve vrs_calib_json_path."""
    gw.GATEWAY_CONFIG["calib_paths"] = {}
    gw.GATEWAY_CONFIG["vrs_calib_json_path"] = "/some/fallback.json"
    assert gw.resolve_calib_path("A") == "/some/fallback.json"


def test_resolve_calib_path_side_b(tmp_calib_files):
    """Mat B: lay tu calib_paths["B"]."""
    path = gw.resolve_calib_path("B")
    assert path == tmp_calib_files["calib_b"]


def test_resolve_calib_path_side_b_missing_raises(cfg_backup):
    """Mat B nhung khong co calib_paths["B"] -> HTTPException 400."""
    gw.GATEWAY_CONFIG["calib_paths"] = {"A": "/some/a.json"}
    with pytest.raises(HTTPException) as exc:
        gw.resolve_calib_path("B")
    assert exc.value.status_code == 400
    assert "mặt B" in exc.value.detail


def test_resolve_calib_path_case_insensitive(tmp_calib_files):
    """board_side "a" va "b" (lowercase) van hoat dong."""
    assert gw.resolve_calib_path("a") == tmp_calib_files["calib_a"]
    assert gw.resolve_calib_path("b") == tmp_calib_files["calib_b"]


# ==============================================================================
# Tests: resolve_offset_path
# ==============================================================================

def test_resolve_offset_path_side_a(tmp_calib_files):
    path = gw.resolve_offset_path("A")
    assert path == tmp_calib_files["offset_a"]


def test_resolve_offset_path_side_a_fallback(cfg_backup):
    gw.GATEWAY_CONFIG["offset_paths"] = {}
    gw.GATEWAY_CONFIG["offset_runtime_json_path"] = "/some/offset_fallback.json"
    assert gw.resolve_offset_path("A") == "/some/offset_fallback.json"


def test_resolve_offset_path_side_b(tmp_calib_files):
    path = gw.resolve_offset_path("B")
    assert path == tmp_calib_files["offset_b"]


def test_resolve_offset_path_side_b_default_name(cfg_backup):
    """Mat B, offset_paths khong khai bao -> ten mac dinh offset_runtime_side_b.json."""
    gw.GATEWAY_CONFIG["offset_paths"] = {}
    path = gw.resolve_offset_path("B")
    assert "offset_runtime_side_b.json" in path


# ==============================================================================
# Tests: validate_board_side
# ==============================================================================

def test_validate_board_side_a_ok(tmp_calib_files):
    """Mat A voi file calib ton tai -> khong raise."""
    gw.validate_board_side("A")  # khong duoc raise


def test_validate_board_side_b_ok(tmp_calib_files):
    """Mat B voi file calib ton tai -> khong raise."""
    gw.validate_board_side("B")


def test_validate_board_side_invalid_value_raises(cfg_backup):
    with pytest.raises(HTTPException) as exc:
        gw.validate_board_side("C")
    assert exc.value.status_code == 400


def test_validate_board_side_b_no_calib_raises(cfg_backup):
    """Mat B nhung khong co calib path -> raise."""
    gw.GATEWAY_CONFIG["calib_paths"] = {}
    with pytest.raises(HTTPException):
        gw.validate_board_side("B")


def test_validate_board_side_b_file_not_exists_raises(cfg_backup):
    """Mat B co calib path nhung file khong ton tai -> raise."""
    gw.GATEWAY_CONFIG["calib_paths"] = {"B": "/nonexistent/path/calib_b.json"}
    with pytest.raises(HTTPException) as exc:
        gw.validate_board_side("B")
    assert "không tồn tại" in exc.value.detail


# ==============================================================================
# Tests: dam bao khong con mirror
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
# Tests: PA2 config model co calib_paths va offset_paths
# ==============================================================================

def test_config_model_has_pa2_fields():
    """GatewayConfigModel phai co calib_paths va offset_paths."""
    model_fields = set(gw.GatewayConfigModel.model_fields.keys())
    assert "calib_paths" in model_fields
    assert "offset_paths" in model_fields


def test_config_model_defaults_empty():
    """calib_paths va offset_paths mac dinh la dict rong."""
    cfg = gw.GatewayConfigModel()
    dump = gw._model_dump(cfg)
    assert dump["calib_paths"] == {}
    assert dump["offset_paths"] == {}
