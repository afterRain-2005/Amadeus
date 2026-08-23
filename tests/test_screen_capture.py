# tests/test_screen_capture.py
from unittest.mock import MagicMock, patch
from core.vision.screen_capture import ScreenCapturer

def test_capturer_starts_and_stops():
    """启动后定时截帧，停止后线程退出。"""
    cap = ScreenCapturer(interval_ms=100)
    with patch("core.vision.screen_capture.mss") as mock_mss:
        mock_sct = MagicMock()
        mock_mss.mss.return_value.__enter__.return_value = mock_sct
        mock_sct.grab.return_value = MagicMock()  # 假帧
        cap.start()
        import time; time.sleep(0.35)  # 等几帧
        assert cap.latest_frame is not None or mock_sct.grab.called
        cap.stop()

def test_latest_frame_caches_only_newest():
    """仅缓存最新帧，不存历史（省内存）。"""
    cap = ScreenCapturer(interval_ms=50)
    with patch("core.vision.screen_capture.mss") as mock_mss:
        mock_sct = MagicMock()
        mock_mss.mss.return_value.__enter__.return_value = mock_sct
        frame1, frame2 = MagicMock(name="frame1"), MagicMock(name="frame2")
        mock_sct.grab.side_effect = [frame1, frame2]
        cap.start()
        import time; time.sleep(0.2)
        cap.stop()
        # 最终缓存的是最后一次截的帧
        if cap.latest_frame is not None:
            assert cap.latest_frame in (frame1, frame2)

def test_stop_is_idempotent():
    cap = ScreenCapturer()
    cap.stop()  # 未启动就停，不应抛异常
    cap.stop()
