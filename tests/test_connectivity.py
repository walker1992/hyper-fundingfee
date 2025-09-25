import os
import pytest

from hyperliquid.info import Info
from hyperliquid.utils import constants


@pytest.mark.skipif(os.environ.get("HL_ONLINE", "0") != "1", reason="Set HL_ONLINE=1 to run connectivity test")
def test_info_connectivity():
    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    sm = info.spot_meta()
    pm = info.meta()
    assert isinstance(sm, dict) and "tokens" in sm
    assert isinstance(pm, dict) and "universe" in pm


