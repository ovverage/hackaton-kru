"""Pure input policy, independently testable without installing OS hooks."""


def blocked_key(vk: int, *, ctrl=False, alt=False, shift=False, locked=False):
    # Keep typing/Tab/Enter available in the teacher password field.
    if vk in (0x5B, 0x5C, 0x2C):  # Windows, PrintScreen
        return True
    if alt and vk in (0x09, 0x1B, 0x73, 0x20):  # task switch, close, system menu
        return True
    if ctrl and vk in (0x1B,):
        return True
    if ctrl and vk in (0x43, 0x56, 0x58, 0x2D):  # clipboard
        return True
    if shift and vk in (0x2D, 0x2E):
        return True
    if not locked:
        if vk in (0x75, 0x7A, 0x7B, 0x5D):  # F6, F11, F12, context menu
            return True
        if ctrl and (vk in (0x09, 0x21, 0x22) or 0x30 <= vk <= 0x39):
            return True
        if ctrl and vk in (0x4C, 0x4E, 0x54, 0x57, 0x4F, 0x50, 0x53, 0x55, 0x4A, 0x48):
            return True  # address, tabs, files, print, source, downloads, history
        if ctrl and shift and vk in (0x49, 0x4A, 0x43):
            return True
        if alt and (vk in (0x25, 0x27, 0x24, 0x44) or 0x30 <= vk <= 0x39):
            return True
    return False
