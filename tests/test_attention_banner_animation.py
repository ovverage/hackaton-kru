"""Guarded Qt banner timing and Z order, without camera or global input hooks."""

from copy import deepcopy
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(scope="module")
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


class NativeOrder:
    """Model HWND_TOPMOST/insert-after semantics, including Qt raise_ calls."""

    def __init__(self, target):
        self.target = target
        self.foreground = target
        self.styles = {target: 0x8}  # WS_EX_TOPMOST
        self.order = [target]  # Front to back.
        self.calls = []
        self.failures = set()

    def GetWindowLongW(self, hwnd, index):
        assert index == -20  # GWL_EXSTYLE
        return self.styles.get(hwnd, 0x8)

    def IsWindow(self, hwnd):
        return bool(hwnd)

    def SetWindowPos(self, hwnd, after, x, y, width, height, flags):
        self.calls.append((hwnd, after, flags))
        if (hwnd, after) in self.failures:
            return 0
        if flags & 0x4:  # SWP_NOZORDER
            return 1
        if hwnd in self.order:
            self.order.remove(hwnd)
        if after in (-1, 0):
            self.order.insert(0, hwnd)
            if after == -1:
                self.styles[hwnd] = self.styles.get(hwnd, 0) | 0x8
        elif after == -2:
            self.order.append(hwnd)
            self.styles[hwnd] = self.styles.get(hwnd, 0) & ~0x8
        else:
            assert after in self.order, "insert-after HWND must already exist"
            self.order.insert(self.order.index(after) + 1, hwnd)
        return 1

    def GetSystemMetrics(self, _index):
        return 0

    def OpenClipboard(self, _owner):
        return False

    def GetForegroundWindow(self):
        return self.foreground

    def SetForegroundWindow(self, hwnd):
        self.foreground = hwnd
        return 1

    def GetAncestor(self, hwnd, _flag):
        return hwnd

    def banner_above_exam(self, banner):
        return self.order.index(int(banner.winId())) < self.order.index(self.target)


@pytest.fixture
def scene(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QWidget
    from agent.client import Agent, atomic_json
    from agent.exam_ui import ExamController, GazeWarning
    from agent.i18n import set_language
    from agent.windows_guard import WindowsGuard, WindowTarget

    set_language("ru", persist=False)
    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "fixture"})
    agent = Agent(tmp_path)
    agent.environment = {"kind": "APP", "guarded": True}
    agent.journal["exam_id"] = "banner-animation-test"
    agent.capabilities.update(camera=True, gaze=True)
    agent.gaze_diagnostics = {
        "source": "public_gaze_model", "reference_ready": True,
        "gaze_tracking_status": "tracked", "gaze_observed_direction": "SCREEN",
        "head_reference_ready": True, "head_tracking_status": "tracked",
        "head_warning": False, "head_extreme": False,
    }
    target = WindowTarget(42, 123, "Exam", "exam.exe", 1)
    agent.guard_target = target.public()
    native = NativeOrder(target.hwnd)
    guard = WindowsGuard.__new__(WindowsGuard)
    guard.u = native
    guard.target, guard.hooks = target, [101, 102]
    guard.locked = guard.desktop = guard.attempted = guard.teacher_requested = False
    guard.overlay_handles = set()
    guard.fullscreen_pending_until = None
    guard.valid = Mock(return_value=True)
    guard.is_fullscreen = Mock(return_value=True)
    guard.stop = Mock()
    parent = QWidget()
    with patch("agent.windows_guard.WindowsGuard", return_value=guard):
        controller = ExamController(agent, parent)
    # Linux does not construct a Windows guard; use the same native fake there.
    controller.guard = guard
    controller.timer.stop()
    controller.environment_timer.stop()
    actual_raise = GazeWarning.raise_

    def tracked_raise(widget):
        actual_raise(widget)
        # Offscreen Qt cannot alter a native Windows stack. Reflect the same
        # operation here so the pre-fix pipeline passes its active phase but
        # fails when the exam obscures the fading banner.
        native.SetWindowPos(int(widget.winId()), -1, 0, 0, 0, 0, 0x13)

    monkeypatch.setattr(GazeWarning, "raise_", tracked_raise)
    started = time.monotonic()
    clock = [started]
    agent.last_synced_at = started
    agent.engine.start()
    monkeypatch.setattr("agent.exam_ui.time.monotonic", lambda: clock[0])

    def tick(offset, *, head=False, gaze="SCREEN"):
        clock[0] = started + offset
        agent.gaze_diagnostics.update(head_warning=head, gaze_observed_direction=gaze)
        controller.tick()
        assert controller.gaze_warnings
        return controller.gaze_warnings[0]

    result = SimpleNamespace(agent=agent, controller=controller, native=native, tick=tick)
    try:
        yield result
    finally:
        controller.release()
        for widget in controller.surfaces + controller.gaze_warnings:
            widget.hide()
            widget.deleteLater()
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        agent.http.close()
        set_language("ru", persist=False)


def test_guarded_banner_holds_across_head_gaze_and_one_second_clear_gap(scene):
    state = deepcopy(scene.agent.engine.state.public())
    banner = scene.tick(0, head=True)
    banner.fade.setCurrentTime(banner.fade.duration())
    for at, head, gaze in ((.2, False, "LEFT"), (.4, False, "SCREEN"),
                           (.8, True, "SCREEN"), (.9, False, "SCREEN"),
                           (1.799, False, "SCREEN")):
        assert scene.tick(at, head=head, gaze=gaze) is banner
        assert banner.isVisible() and banner.windowOpacity() == 1
        assert scene.native.banner_above_exam(banner)
    assert scene.agent.engine.state.public() == state
    assert scene.agent.engine.seconds == 0
    scene.tick(1.801)
    assert banner.isVisible() and banner.fade.endValue() == 0
    banner.fade.setCurrentTime(banner.fade.duration())
    assert not banner.isVisible()


def test_guarded_fade_stays_above_exam_and_reentry_preserves_current_opacity(scene):
    banner = scene.tick(0, gaze="RIGHT")
    banner.fade.setCurrentTime(banner.fade.duration())
    scene.tick(.1)
    scene.tick(.999)
    assert banner.isVisible() and banner.windowOpacity() == 1
    scene.tick(1.001)
    banner.fade.setCurrentTime(banner.fade.duration() // 2)
    fading_opacity = banner.windowOpacity()
    assert 0 < fading_opacity < 1
    scene.tick(1.05)
    assert banner.isVisible() and scene.native.banner_above_exam(banner)
    assert banner.windowOpacity() == pytest.approx(fading_opacity, abs=.01)
    scene.tick(1.1, head=True)
    assert banner.isVisible() and banner.fade.endValue() == 1
    assert banner.fade.startValue() == pytest.approx(fading_opacity, abs=.01)
    assert banner.windowOpacity() == pytest.approx(fading_opacity, abs=.01)
    banner.fade.setCurrentTime(banner.fade.duration() // 2)
    resumed_opacity = banner.windowOpacity()
    assert fading_opacity < resumed_opacity < 1
    scene.tick(1.2, gaze="DOWN")
    assert banner.windowOpacity() == pytest.approx(resumed_opacity, abs=.01)
    assert banner.fade.currentTime() == banner.fade.duration() // 2
    assert scene.native.banner_above_exam(banner)


@pytest.mark.parametrize("reason", ["lock", "camera_fault"])
def test_lock_or_camera_fault_clears_banner_immediately_even_mid_animation(scene, reason):
    banner = scene.tick(0, head=True)
    banner.fade.setCurrentTime(banner.fade.duration() // 2)
    assert banner.isVisible() and 0 < banner.windowOpacity() < 1
    if reason == "lock":
        scene.agent.engine.lock("PHONE_DETECTED")
    else:
        scene.agent.camera_fault = True
    scene.tick(.05, head=True)
    assert not banner.isVisible() and banner.windowOpacity() == 0
    assert not scene.controller.warning_display.parts
    assert scene.agent.engine.seconds == 0


def test_native_stacking_repairs_removed_topmost_and_keeps_warning_above_exam(scene):
    banner = scene.tick(0, head=True)
    scene.native.styles[scene.native.target] = 0
    scene.native.calls.clear()
    scene.tick(.2, head=True)
    assert scene.native.styles[scene.native.target] & 0x8
    assert scene.native.banner_above_exam(banner)
    assert any(hwnd == scene.native.target and after == -1
               for hwnd, after, _flags in scene.native.calls)
    assert all(flags & 0x10 for _hwnd, _after, flags in scene.native.calls)
    scene.native.calls.clear()
    scene.tick(.3, head=True)
    assert not any(hwnd == scene.native.target and after == -1
                   for hwnd, after, _flags in scene.native.calls)
    assert scene.native.banner_above_exam(banner)


def native_stack_guard():
    from agent.windows_guard import WindowsGuard, WindowTarget

    guard = WindowsGuard.__new__(WindowsGuard)
    guard.target = WindowTarget(42, 123, "Exam", "exam.exe", 1)
    guard.u = NativeOrder(guard.target.hwnd)
    return guard


def test_native_stacking_keeps_two_warnings_above_exam_and_unrelated_topmost():
    guard = native_stack_guard()
    first, second, unrelated = 501, 502, 900
    guard.u.order = [unrelated, 42, first, second]
    guard.stack_exam_below([first, second])
    assert guard.u.order.index(first) < guard.u.order.index(42)
    assert guard.u.order.index(second) < guard.u.order.index(42)
    assert guard.u.order.index(42) < guard.u.order.index(unrelated)
    # Another application's topmost popup appearing later must not remain above
    # the exam, and fixing that must preserve both of our own warning windows.
    guard.u.SetWindowPos(unrelated, -1, 0, 0, 0, 0, 0x13)
    guard.u.calls.clear()
    guard.stack_exam_below([first, second])
    assert guard.u.order.index(first) < guard.u.order.index(42)
    assert guard.u.order.index(second) < guard.u.order.index(42)
    assert guard.u.order.index(42) < guard.u.order.index(unrelated)
    assert not any(hwnd == 42 and after == -1 for hwnd, after, _flags in guard.u.calls)


def test_native_stacking_without_warning_restores_exam_topmost_protection():
    guard = native_stack_guard()
    guard.u.styles[42] = 0
    guard.u.order = [900, 42]
    guard.stack_exam_below([])
    assert guard.u.styles[42] & 0x8
    assert guard.u.order == [42, 900]
    assert guard.u.calls[-1] == (42, -1, 0x13)


@pytest.mark.parametrize("failure", [(501, -1), (42, 501)])
def test_failed_warning_stacking_restores_exam_to_top_and_reports_failure(failure):
    guard = native_stack_guard()
    guard.u.order = [900, 501, 42]
    guard.u.failures.add(failure)
    with pytest.raises(OSError):
        guard.stack_exam_below([501])
    assert guard.u.styles[42] & 0x8
    assert guard.u.order[0] == 42
    assert guard.u.calls[-1] == (42, -1, 0x13)


def test_guard_security_lock_hides_warning_in_same_tick(scene, monkeypatch):
    banner = scene.tick(0, gaze="LEFT")
    banner.fade.setCurrentTime(banner.fade.duration())
    assert banner.isVisible()
    # A native remote-session detection happens after the initial open snapshot
    # and banner update in controller.tick. It must still clear the banner now.
    monkeypatch.setattr(scene.native, "GetSystemMetrics", lambda _index: 1)
    scene.tick(.2, gaze="LEFT")
    assert scene.agent.engine.state.access == "LOCKED"
    assert scene.agent.engine.state.reason == "REMOTE_SESSION"
    assert not banner.isVisible() and banner.windowOpacity() == 0
    assert not scene.controller.warning_display.parts
    scene.tick(.3, gaze="LEFT")
    assert scene.controller.surfaces
    assert all(surface.isVisible() for surface in scene.controller.surfaces)
