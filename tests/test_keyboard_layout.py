"""Language shortcuts must not expose the Windows shell or alter escape policy."""

from agent.keyboard_layout import LayoutShortcut, next_layout
from agent.guard_policy import blocked_key


def test_cycles_only_installed_layouts_without_changing_their_identity():
    assert next_layout([1049, 1033, 1087], 1049) == 1033
    assert next_layout([1049, 1033, 1087], 1087) == 1049
    assert next_layout([1049, 1049, 1033], 1049) == 1033
    assert next_layout([], 1049) is None


def test_win_space_consumes_keys_and_switches_once_per_press():
    keys = LayoutShortcut()
    assert keys.handle(0x5B, True) == 'consume'
    assert keys.handle(0x20, True) == 'switch'
    assert keys.handle(0x20, True) == 'consume'  # auto-repeat
    assert keys.handle(0x5B, False) == 'consume'
    assert keys.handle(0x20, False) == 'consume'
    assert keys.handle(0x41, True) is None


def test_other_win_shortcuts_remain_blocked_and_alt_shift_remains_available():
    keys = LayoutShortcut()
    keys.handle(0x5B, True)
    for vk in (0x44, 0x52, 0x45, 0x09):  # Desktop, Run, Explorer, Task View
        assert keys.handle(vk, True) == 'blocked'
        assert keys.handle(vk, False) == 'blocked'
    keys.handle(0x5B, False)
    assert not blocked_key(0x10, alt=True)
    assert not blocked_key(0x12, shift=True)
    assert blocked_key(0x09, alt=True) and blocked_key(0x1B, ctrl=True, shift=True)
    assert blocked_key(0x5B)  # general guard policy is not weakened


def test_both_windows_keys_and_key_release_orders_are_handled():
    keys = LayoutShortcut()
    keys.handle(0x5B, True)
    keys.handle(0x5C, True)
    keys.handle(0x5B, False)
    assert keys.handle(0x20, True) == 'switch'
    assert keys.handle(0x20, False) == 'consume'
    assert keys.handle(0x20, True) == 'switch'
    keys.handle(0x20, False)
    keys.handle(0x5C, False)
    assert keys.handle(0x20, True) is None
