"""Tests for iphonebridge.ancs.parsers — wire-format round-trips."""
from __future__ import annotations

import struct

from iphonebridge.ancs.constants import CommandID, EventFlag, EventID, NotificationAttributeID
from iphonebridge.ancs.parsers import (
    AppAttributes,
    DataSourceEvent,
    Notification,
    NotificationAttributes,
    build_get_app_attributes,
    build_get_notification_attributes,
)


def _attr(attr_id: int, text: str) -> bytes:
    body = text.encode("utf-8")
    return struct.pack("<BH", attr_id, len(body)) + body


class TestNotification:
    def test_parse_basic(self):
        # eid=0 Added, flags=0, cat=1 IncomingCall, count=5, uid=42
        raw = struct.pack("<BBBBI", EventID.NotificationAdded, 0, 1, 5, 42)
        n = Notification.parse(raw)
        assert n.id == 42
        assert n.type == EventID.NotificationAdded
        assert n.category == 1
        assert n.category_count == 5
        assert not n.is_preexisting
        assert n.is_fresh

    def test_preexisting_flag(self):
        raw = struct.pack("<BBBBI", EventID.NotificationAdded, EventFlag.PreExisting, 0, 0, 1)
        n = Notification.parse(raw)
        assert n.is_preexisting
        assert not n.is_fresh

    def test_silent_and_actions(self):
        flags = EventFlag.Silent | EventFlag.PositiveAction | EventFlag.NegativeAction
        raw = struct.pack("<BBBBI", EventID.NotificationModified, flags, 4, 0, 99)
        n = Notification.parse(raw)
        assert n.is_silent
        assert n.has_positive_action
        assert n.has_negative_action

    def test_too_short_raises(self):
        try:
            Notification.parse(b"\x00\x01")
            raise AssertionError("should have raised")
        except ValueError:
            pass


class TestNotificationAttributes:
    def test_parse_minimal(self):
        uid = 123
        body = struct.pack("<I", uid)
        body += _attr(NotificationAttributeID.AppIdentifier, "com.apple.mobileSMS")
        body += _attr(NotificationAttributeID.Title, "Maddie")
        body += _attr(NotificationAttributeID.Subtitle, "")
        body += _attr(NotificationAttributeID.Message, "hello")
        attrs = NotificationAttributes.parse(body)
        assert attrs.id == uid
        assert attrs.app_id == "com.apple.mobileSMS"
        assert attrs.title == "Maddie"
        assert attrs.message == "hello"
        assert attrs.positive_action is None

    def test_parse_with_actions(self):
        uid = 7
        body = struct.pack("<I", uid)
        body += _attr(NotificationAttributeID.AppIdentifier, "com.test.app")
        body += _attr(NotificationAttributeID.Title, "T")
        body += _attr(NotificationAttributeID.Subtitle, "S")
        body += _attr(NotificationAttributeID.Message, "M")
        body += _attr(NotificationAttributeID.PositiveActionLabel, "Reply")
        body += _attr(NotificationAttributeID.NegativeActionLabel, "Dismiss")
        attrs = NotificationAttributes.parse(body)
        assert attrs.positive_action == "Reply"
        assert attrs.negative_action == "Dismiss"


class TestAppAttributes:
    def test_parse_with_display_name(self):
        app_id = "com.apple.mobileSMS"
        display = "Messages"
        body = app_id.encode() + b"\0" + _attr(0, display)  # AppAttributeID.DisplayName == 0
        parsed = AppAttributes.parse(body)
        assert parsed.app_id == app_id
        assert parsed.app_name == display

    def test_parse_no_display_name(self):
        body = b"com.test.app\x00"
        parsed = AppAttributes.parse(body)
        assert parsed.app_id == "com.test.app"
        assert parsed.app_name == "<not installed>"


class TestDataSourceEvent:
    def test_parse(self):
        raw = bytes([CommandID.GetNotificationAttributes]) + b"\x01\x02\x03"
        ev = DataSourceEvent.parse(raw)
        assert ev.type == CommandID.GetNotificationAttributes
        assert ev.body == b"\x01\x02\x03"

    def test_empty_raises(self):
        try:
            DataSourceEvent.parse(b"")
            raise AssertionError("should have raised")
        except ValueError:
            pass


class TestBuilders:
    def test_build_get_notification_attributes(self):
        pkt = build_get_notification_attributes(42, want_positive=True, want_negative=False)
        assert pkt[0] == CommandID.GetNotificationAttributes
        assert struct.unpack("<I", pkt[1:5])[0] == 42
        # Should contain Title, Subtitle, Message and Positive but not Negative
        assert NotificationAttributeID.Title in pkt
        assert NotificationAttributeID.PositiveActionLabel in pkt
        assert NotificationAttributeID.NegativeActionLabel not in pkt

    def test_build_get_app_attributes(self):
        pkt = build_get_app_attributes("com.example.app")
        assert pkt[0] == CommandID.GetAppAttributes
        assert b"com.example.app\x00" in pkt

    def test_build_clamps_max(self):
        # Large max should be clamped to USHORT_MAX
        pkt = build_get_notification_attributes(1, message_max=99999)
        # Message max is u16-le after Message attribute id
        # Find Message attr and read its u16
        idx = pkt.index(NotificationAttributeID.Message)
        max_val = struct.unpack("<H", pkt[idx + 1: idx + 3])[0]
        assert max_val == 65535
