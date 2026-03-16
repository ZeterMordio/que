"""AppKit entrypoint for the packaged `que` menu bar app."""
from __future__ import annotations

import fcntl
import json
import os
import sys
from dataclasses import dataclass

import AppKit  # type: ignore
import objc  # type: ignore
from Foundation import NSObject, NSRunLoop, NSTimer  # type: ignore
from PyObjCTools import AppHelper  # type: ignore

from ..service.paths import service_state_dir
from .chrome import frontmost_bundle_id, read_google_chrome_active_tab_url
from .controller import (
    CompletionNotice,
    MenuBarController,
    status_item_title_for_phase,
)
from .diagnostics import diagnostics_log_path, log_diagnostic
from .music import notify_sync_result, open_track_in_music
from .settings import SettingsWindowController

_APP_DELEGATE = None
_MENUBAR_LOCK_HANDLE = None


@dataclass(frozen=True)
class MenuRowSpec:
    """Rendered menu row specification.

    Attributes:
        kind: One of `disabled`, `action`, `track`, or `separator`.
        title: User-visible row text.
        action: Selector name for clickable rows.
        represented_object: Encoded payload for track rows.
    """

    kind: str
    title: str = ""
    action: str | None = None
    represented_object: str | None = None


class MenuBarApplication(NSObject):
    """Own the AppKit status item, menu, and window bindings."""

    @objc.IBAction
    def applicationDidFinishLaunching_(self, _notification):  # noqa: N802
        """Create the status item and start periodic refresh."""
        self.controller = MenuBarController(
            frontmost_bundle_id=frontmost_bundle_id,
            read_chrome_url=read_google_chrome_active_tab_url,
        )
        self.settings_controller = SettingsWindowController.alloc().initWithController_(
            self.controller
        )
        self.status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        self.menu = AppKit.NSMenu.alloc().initWithTitle_("Que Menu")
        self.menu.setAutoenablesItems_(False)
        self.menu.setDelegate_(self)
        self._last_menu_signature = None

        button = self.status_item.button()
        button.setTitle_("que")
        button.setToolTip_("que")
        button.setTarget_(self)
        button.setAction_(_selector(self.handleStatusItemClick_))
        button.sendActionOn_(_mouse_event_mask())

        log_diagnostic(
            "app_started",
            pid=os.getpid(),
            diagnostics_log=str(diagnostics_log_path()),
        )
        self.refresh_(None)
        self.timer = _install_refresh_timer(self, interval_seconds=0.5)

    @objc.IBAction
    def menuWillOpen_(self, _menu) -> None:  # noqa: N802
        """Force one refresh when the dropdown becomes visible."""
        log_diagnostic("menu_visibility", state="open")
        self.refresh_(None)

    @objc.IBAction
    def menuDidClose_(self, _menu) -> None:  # noqa: N802
        """Record menu-close diagnostics."""
        log_diagnostic("menu_visibility", state="closed")

    @objc.IBAction
    def handleStatusItemClick_(self, _sender) -> None:  # noqa: N802
        """Dispatch left click to the menu and right click to quick sync."""
        event = AppKit.NSApp.currentEvent()
        if _is_right_click(event):
            log_diagnostic("status_item_click", button="right")
            try:
                outcome = self.controller.sync_from_inputs()
                self._set_button_tooltip(outcome.message)
            except Exception as exc:
                message = f"Quick sync failed: {exc}"
                self.controller.set_feedback(message)
                self._set_button_tooltip(message)
            self.refresh_(None)
            return

        log_diagnostic("status_item_click", button="left")
        self.refresh_(None)
        self.status_item.popUpStatusItemMenu_(self.menu)

    @objc.IBAction
    def refresh_(self, _sender) -> None:  # noqa: N802
        """Refresh button state and menu contents from the daemon."""
        try:
            snapshot = self.controller.refresh_snapshot()
        except Exception as exc:
            message = f"Daemon unavailable: {exc}"
            self.controller.set_feedback(message)
            self._set_button_title(snapshot=None, errored=True)
            self._set_button_tooltip(message)
            self._rebuild_error_menu(str(exc))
            self._deliver_notifications(self.controller.drain_notifications())
            return

        self._set_button_title(snapshot=snapshot, errored=False)
        self._set_button_tooltip(snapshot.detail)
        self._rebuild_menu(snapshot)
        self._deliver_notifications(self.controller.drain_notifications())

    @objc.IBAction
    def syncNow_(self, _sender) -> None:  # noqa: N802
        """Start a quick sync from the normal menu action."""
        log_diagnostic("menu_action", action="sync_now")
        try:
            outcome = self.controller.sync_from_inputs()
            self._set_button_tooltip(outcome.message)
        except Exception as exc:
            message = f"Quick sync failed: {exc}"
            self.controller.set_feedback(message)
            self._set_button_tooltip(message)
        self.refresh_(None)

    @objc.IBAction
    def openTrack_(self, sender) -> None:  # noqa: N802
        """Open the represented track in Music.app."""
        artist, title = _decode_track(sender.representedObject())
        label = f"{artist} - {title}" if artist and title else artist or title or "track"
        ok = open_track_in_music(artist, title)
        if ok:
            message = f"Opened in Music: {label}"
        else:
            message = f"Track not found in Music: {label}"
        log_diagnostic("menu_action", action="open_track", artist=artist, title=title, ok=ok)
        self.controller.set_feedback(message)
        self._set_button_tooltip(message)
        self.refresh_(None)

    @objc.IBAction
    def openSettings_(self, _sender) -> None:  # noqa: N802
        """Open the dedicated settings window."""
        log_diagnostic("menu_action", action="open_settings")
        self.settings_controller.show()

    @objc.IBAction
    def quit_(self, _sender) -> None:  # noqa: N802
        """Terminate the menu-bar app."""
        log_diagnostic("menu_action", action="quit")
        AppKit.NSApp.terminate_(None)

    @objc.python_method
    def _rebuild_menu(self, snapshot) -> None:
        """Rebuild the menu from the latest controller snapshot."""
        self._apply_menu_rows(self._menu_rows(snapshot))
        self._log_menu_snapshot(snapshot)

    @objc.python_method
    def _rebuild_error_menu(self, message: str) -> None:
        """Render a minimal error menu when the daemon is unavailable."""
        self._apply_menu_rows(
            [
                MenuRowSpec(kind="disabled", title="Daemon unavailable"),
                MenuRowSpec(kind="disabled", title=message),
                MenuRowSpec(kind="separator"),
                MenuRowSpec(kind="action", title="Settings…", action="openSettings:"),
                MenuRowSpec(kind="action", title="Quit", action="quit:"),
            ]
        )
        log_diagnostic("menu_snapshot", title="Daemon unavailable", detail=message)

    @objc.python_method
    def _menu_rows(self, snapshot) -> list[MenuRowSpec]:
        """Build the latest menu rows from one controller snapshot."""
        rows = [MenuRowSpec(kind="disabled", title=snapshot.detail)]
        rows.extend(MenuRowSpec(kind="disabled", title=line) for line in snapshot.status_lines)
        rows.extend(
            [
                MenuRowSpec(kind="separator"),
                MenuRowSpec(
                    kind="action",
                    title="Sync Clipboard / Active Tab",
                    action="syncNow:",
                ),
            ]
        )

        if snapshot.primary_track is not None:
            rows.append(
                MenuRowSpec(
                    kind="track",
                    title=f"Open Last Finished · {snapshot.primary_track.label()} ↗",
                    action="openTrack:",
                    represented_object=_encode_track(
                        snapshot.primary_track.artist,
                        snapshot.primary_track.title,
                    ),
                )
            )

        rows.append(MenuRowSpec(kind="separator"))
        if snapshot.history:
            for item in snapshot.history:
                rows.append(
                    MenuRowSpec(
                        kind="disabled",
                        title=(
                            f"{item.status_label} · "
                            f"{item.row_label} · "
                            f"{item.relative_time}"
                        ),
                    )
                )
        else:
            rows.append(MenuRowSpec(kind="disabled", title="No recent tracks"))

        rows.extend(
            [
                MenuRowSpec(kind="separator"),
                MenuRowSpec(kind="action", title="Settings…", action="openSettings:"),
                MenuRowSpec(kind="action", title="Quit", action="quit:"),
            ]
        )
        return rows

    @objc.python_method
    def _apply_menu_rows(self, rows: list[MenuRowSpec]) -> None:
        """Apply menu rows in place so visible menus keep updating live."""
        while self.menu.numberOfItems() > len(rows):
            self.menu.removeItemAtIndex_(self.menu.numberOfItems() - 1)

        for index, row in enumerate(rows):
            item = self.menu.itemAtIndex_(index) if index < self.menu.numberOfItems() else None
            if row.kind == "separator":
                if item is None or not item.isSeparatorItem():
                    item = self._replace_menu_item(index, AppKit.NSMenuItem.separatorItem())
                continue

            if item is None or item.isSeparatorItem():
                item = self._replace_menu_item(index, self._blank_item())

            item.setTitle_(row.title)
            item.setRepresentedObject_(row.represented_object)
            if row.kind == "disabled":
                item.setTarget_(None)
                item.setAction_(None)
                item.setEnabled_(False)
                continue

            item.setTarget_(self)
            # Reason: update action every refresh so live menus keep working even
            # if an existing row changed from disabled -> clickable.
            item.setAction_(_selector(getattr(self, str(row.action).replace(":", "_"))))
            item.setEnabled_(True)

    @objc.python_method
    def _replace_menu_item(self, index: int, item):
        """Insert or replace one menu item at a stable index."""
        if index >= self.menu.numberOfItems():
            self.menu.addItem_(item)
            return item
        self.menu.insertItem_atIndex_(item, index)
        self.menu.removeItemAtIndex_(index + 1)
        return item

    @objc.python_method
    def _blank_item(self):
        """Create a neutral menu item that can be reconfigured in place."""
        return AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")

    @objc.python_method
    def _set_button_title(self, *, snapshot, errored: bool) -> None:
        """Set the small menu bar title."""
        title = status_item_title_for_phase(
            phase=None if snapshot is None else snapshot.active_phase,
            queued=bool(snapshot and snapshot.queue_length > 0 and snapshot.active_job_id is None),
            errored=errored,
        )
        self.status_item.button().setTitle_(title)

    @objc.python_method
    def _set_button_tooltip(self, text: str) -> None:
        """Set the current hover tooltip."""
        self.status_item.button().setToolTip_(text)

    @objc.python_method
    def _deliver_notifications(self, notices: list[CompletionNotice]) -> None:
        """Show any queued completion notifications."""
        for notice in notices:
            notify_sync_result(notice.title, notice.message, success=notice.success)
            log_diagnostic(
                "notification_sent",
                title=notice.title,
                message=notice.message,
                success=notice.success,
                track=(notice.track.label() if notice.track else None),
            )

    @objc.python_method
    def _log_menu_snapshot(self, snapshot) -> None:
        """Write a diagnostics entry when the rendered menu changes."""
        signature = (
            snapshot.title,
            snapshot.detail,
            tuple(snapshot.status_lines),
            (
                snapshot.primary_track.artist,
                snapshot.primary_track.title,
                snapshot.primary_track.status,
            )
            if snapshot.primary_track is not None
            else None,
            tuple(
                (
                    item.status,
                    item.status_label,
                    item.row_label,
                    item.artist,
                    item.title,
                    item.relative_time,
                    item.openable,
                )
                for item in snapshot.history
            ),
        )
        if signature == self._last_menu_signature:
            return
        self._last_menu_signature = signature
        log_diagnostic(
            "menu_snapshot",
            title=snapshot.title,
            detail=snapshot.detail,
            status_lines=snapshot.status_lines,
            primary_track=(snapshot.primary_track.label() if snapshot.primary_track else None),
            history=[
                {
                    "status": item.status,
                    "status_label": item.status_label,
                    "row_label": item.row_label,
                    "artist": item.artist,
                    "title": item.title,
                    "relative_time": item.relative_time,
                    "openable": item.openable,
                }
                for item in snapshot.history
            ],
        )


def main() -> None:
    """Launch the AppKit menu-bar application."""
    global _APP_DELEGATE, _MENUBAR_LOCK_HANDLE
    _MENUBAR_LOCK_HANDLE = _acquire_menubar_lock()
    if _MENUBAR_LOCK_HANDLE is None:
        print("que-menu already running", file=sys.stderr)
        return
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    _APP_DELEGATE = MenuBarApplication.alloc().init()
    app.setDelegate_(_APP_DELEGATE)
    AppHelper.runEventLoop()


@objc.python_method
def _mouse_event_mask() -> int:
    """Return the event mask needed to receive one left/right click callback."""
    if hasattr(AppKit, "NSEventMaskLeftMouseUp"):
        return AppKit.NSEventMaskLeftMouseUp | AppKit.NSEventMaskRightMouseUp
    return AppKit.NSLeftMouseUpMask | AppKit.NSRightMouseUpMask


@objc.python_method
def _is_right_click(event) -> bool:
    """Return True when the current status-item click is a right click."""
    if event is None:
        return False
    modifier_flags = int(event.modifierFlags()) if hasattr(event, "modifierFlags") else 0
    control_pressed = bool(
        modifier_flags & getattr(AppKit, "NSEventModifierFlagControl", 0)
    )
    if hasattr(AppKit, "NSEventTypeRightMouseUp"):
        return (
            event.type() in {AppKit.NSEventTypeRightMouseDown, AppKit.NSEventTypeRightMouseUp}
            or event.buttonNumber() == 1
            or control_pressed
        )
    return (
        event.type() in {AppKit.NSRightMouseDown, AppKit.NSRightMouseUp}
        or event.buttonNumber() == 1
        or control_pressed
    )


@objc.python_method
def _selector(method):
    """Return an Objective-C selector object for one bound action method."""
    return objc.selector(method, signature=b"v@:@")


@objc.python_method
def _install_refresh_timer(target, *, interval_seconds: float):
    """Install the periodic refresh timer in normal and menu-tracking modes.

    Args:
        target: Timer target object implementing `refresh_`.
        interval_seconds: Refresh cadence in seconds.

    Returns:
        Installed `NSTimer` instance.
    """
    timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
        interval_seconds,
        target,
        "refresh:",
        None,
        True,
    )
    if hasattr(timer, "setTolerance_"):
        timer.setTolerance_(min(0.2, interval_seconds / 2))
    run_loop = NSRunLoop.mainRunLoop()
    run_loop.addTimer_forMode_(timer, AppKit.NSRunLoopCommonModes)
    if hasattr(AppKit, "NSEventTrackingRunLoopMode"):
        run_loop.addTimer_forMode_(timer, AppKit.NSEventTrackingRunLoopMode)
    return timer


@objc.python_method
def _acquire_menubar_lock():
    """Acquire a lock so only one menu-bar client instance runs."""
    lock_path = service_state_dir() / "menubar.pid"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


@objc.python_method
def _encode_track(artist: str, title: str) -> str:
    """Encode a track payload for an Objective-C represented object."""
    return json.dumps({"artist": artist, "title": title}, ensure_ascii=False)


@objc.python_method
def _decode_track(payload: str | None) -> tuple[str, str]:
    """Decode a represented-object track payload."""
    if not payload:
        return "", ""
    try:
        data = json.loads(str(payload))
    except json.JSONDecodeError:
        return "", ""
    return str(data.get("artist") or ""), str(data.get("title") or "")
