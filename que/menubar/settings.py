"""Settings window for the macOS `que` menu bar app."""
from __future__ import annotations

import objc  # type: ignore
from AppKit import (  # type: ignore
    NSAlert,
    NSApp,
    NSBackingStoreBuffered,
    NSButton,
    NSControlStateValueOn,
    NSMakeRect,
    NSTextField,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject  # type: ignore


class SettingsWindowController(NSObject):
    """Own the v1 settings window and field bindings."""

    def initWithController_(self, controller):  # noqa: N802
        """Initialize with a thin menu-bar controller."""
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None
        self.controller = controller
        self.window = None
        self.music_checkbox = None
        self.threshold_field = None
        self.staging_field = None
        self.destination_field = None
        self.path_label = None
        self.message_label = None
        return self

    @objc.python_method
    def show(self) -> None:
        """Show the settings window and load fresh daemon-backed config."""
        if self.window is None:
            self._build_window()
        self._load_fields()
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    @objc.IBAction
    def save_(self, _sender) -> None:  # noqa: N802
        """Save the current field values through the daemon."""
        try:
            payload = self.controller.save_settings(
                use_music_app=self.music_checkbox.state() == NSControlStateValueOn,
                fuzzy_threshold=int(self.threshold_field.stringValue()),
                staging_dir=str(self.staging_field.stringValue()).strip(),
                import_destination=str(self.destination_field.stringValue()).strip(),
            )
        except Exception as exc:
            self._show_error(f"Could not save settings:\n{exc}")
            return

        self._populate_fields(payload)
        self.message_label.setStringValue_("Settings saved")

    @objc.IBAction
    def close_(self, _sender) -> None:  # noqa: N802
        """Close the settings window."""
        self.window.orderOut_(None)

    @objc.python_method
    def _build_window(self) -> None:
        """Create the programmatic window and v1 form fields."""
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, 500.0, 280.0),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Que Settings")
        self.window.setReleasedWhenClosed_(False)
        content = self.window.contentView()

        self.music_checkbox = self._make_checkbox(
            frame=NSMakeRect(20.0, 228.0, 280.0, 24.0),
            title="Use Apple Music integration",
        )
        content.addSubview_(self.music_checkbox)

        content.addSubview_(
            self._make_label("Fuzzy threshold", NSMakeRect(20.0, 192.0, 120.0, 20.0))
        )
        self.threshold_field = self._make_text_field(NSMakeRect(150.0, 188.0, 80.0, 24.0))
        content.addSubview_(self.threshold_field)

        content.addSubview_(
            self._make_label("Staging directory", NSMakeRect(20.0, 152.0, 120.0, 20.0))
        )
        self.staging_field = self._make_text_field(NSMakeRect(150.0, 148.0, 320.0, 24.0))
        content.addSubview_(self.staging_field)

        content.addSubview_(
            self._make_label("Import destination", NSMakeRect(20.0, 112.0, 120.0, 20.0))
        )
        self.destination_field = self._make_text_field(NSMakeRect(150.0, 108.0, 320.0, 24.0))
        content.addSubview_(self.destination_field)

        content.addSubview_(self._make_label("Config path", NSMakeRect(20.0, 72.0, 120.0, 20.0)))
        self.path_label = self._make_label("", NSMakeRect(150.0, 72.0, 320.0, 20.0))
        content.addSubview_(self.path_label)

        self.message_label = self._make_label(
            "Advanced edits stay in the raw TOML config file.",
            NSMakeRect(20.0, 40.0, 340.0, 20.0),
        )
        content.addSubview_(self.message_label)

        save_button = self._make_button("Save", NSMakeRect(380.0, 18.0, 90.0, 28.0), "save:")
        cancel_button = self._make_button("Close", NSMakeRect(280.0, 18.0, 90.0, 28.0), "close:")
        content.addSubview_(save_button)
        content.addSubview_(cancel_button)

    @objc.python_method
    def _load_fields(self) -> None:
        """Load the current daemon-backed config into the form."""
        payload = self.controller.load_settings()
        self._populate_fields(payload)
        self.message_label.setStringValue_("Advanced edits stay in the raw TOML config file.")

    @objc.python_method
    def _populate_fields(self, payload: dict) -> None:
        """Write a config payload into the form fields."""
        self.music_checkbox.setState_(
            NSControlStateValueOn if payload.get("use_music_app") else 0
        )
        self.threshold_field.setStringValue_(str(payload.get("fuzzy_threshold", "")))
        self.staging_field.setStringValue_(str(payload.get("staging_dir", "")))
        self.destination_field.setStringValue_(str(payload.get("import_destination", "")))
        self.path_label.setStringValue_(str(payload.get("config_path", "")))

    @objc.python_method
    def _show_error(self, message: str) -> None:
        """Show a modal settings error dialog."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Que Settings")
        alert.setInformativeText_(message)
        alert.runModal()

    @objc.python_method
    def _make_label(self, text: str, frame):
        """Create a small static label."""
        label = NSTextField.alloc().initWithFrame_(frame)
        label.setStringValue_(text)
        label.setEditable_(False)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setSelectable_(False)
        return label

    @objc.python_method
    def _make_text_field(self, frame):
        """Create an editable text field."""
        field = NSTextField.alloc().initWithFrame_(frame)
        field.setEditable_(True)
        field.setSelectable_(True)
        return field

    @objc.python_method
    def _make_checkbox(self, *, frame, title: str):
        """Create a checkbox bound to no immediate action."""
        button = NSButton.alloc().initWithFrame_(frame)
        button.setButtonType_(3)
        button.setTitle_(title)
        return button

    @objc.python_method
    def _make_button(self, title: str, frame, action: str):
        """Create a push button bound to this controller."""
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setTarget_(self)
        button.setAction_(objc.selector(getattr(self, action.replace(":", "_")), signature=b"v@:@"))
        return button
