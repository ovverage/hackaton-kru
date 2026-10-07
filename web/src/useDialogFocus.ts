import { useEffect, useRef, type RefObject } from "react";

/** Keep keyboard navigation in the uppermost dialog and return focus on close. */
export function useDialogFocus(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
) {
  const close = useRef(onClose);
  // Capture before React mounts any autoFocus input inside this dialog.
  const opener = useRef(
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  close.current = onClose;
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const controls = () =>
      [
        ...dialog.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]',
        ),
      ].filter((element) => element.offsetParent !== null);
    if (!dialog.contains(document.activeElement))
      (controls()[0] || dialog).focus();
    const keydown = (event: KeyboardEvent) => {
      const dialogs = document.querySelectorAll('[role="dialog"]');
      if (dialogs[dialogs.length - 1] !== dialog) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        close.current();
      }
      if (event.key !== "Tab") return;
      const items = controls();
      const first = items[0],
        last = items[items.length - 1];
      if (!first) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      if (
        !dialog.contains(document.activeElement) ||
        (event.shiftKey
          ? document.activeElement === first
          : document.activeElement === last)
      ) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener("keydown", keydown, true);
    return () => {
      document.removeEventListener("keydown", keydown, true);
      if (opener.current?.isConnected) opener.current.focus();
    };
  }, [ref]);
}
