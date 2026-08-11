#!/usr/bin/python3
# pylint: disable=import-error

import os
import weakref

from JsonSettingsWidgets import SettingsWidget
from gi.repository import Gdk, GdkPixbuf, Gtk


class _ResponsiveLogo(SettingsWidget):
    github_url = "https://github.com/H234598/speed-of-cinnamon"
    asset_name = ""
    top_margin = 10
    bottom_margin = 10
    max_width = 720
    max_height = 220

    def __init__(self, info, key, settings):
        SettingsWidget.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_spacing(0)
        self.set_margin_top(self.top_margin)
        self.set_margin_bottom(self.bottom_margin)
        self.set_hexpand(True)

        self._last_render_size = (0, 0)
        self._source_pixbuf = None
        self._scaled_pixbuf = None
        self._drawing_area = None
        self._logo_box = None
        self._fallback_label = None
        self._fallback_text = info.get("description", "")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_dir, "assets", self.asset_name)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._logo_box = box
        box.set_halign(Gtk.Align.FILL)
        box.set_hexpand(True)
        box.set_tooltip_text("Open the Speed of Cinnamon GitHub repository")

        try:
            self._source_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                logo_path,
                self.max_width,
                self.max_height,
                True,
            )
            self._drawing_area = Gtk.DrawingArea()
            self._drawing_area.set_halign(Gtk.Align.FILL)
            self._drawing_area.set_hexpand(True)
            self._drawing_area.set_size_request(1, 1)
            self._drawing_area.connect("draw", self._on_draw)
            box.pack_start(self._drawing_area, True, True, 0)
            box.connect("size-allocate", self._on_size_allocate)
            self._set_render_size(self.max_width, self.max_height)
        except Exception:
            self._show_fallback()

        event_box = Gtk.EventBox()
        event_box.set_visible_window(False)
        event_box.set_tooltip_text("Open the Speed of Cinnamon GitHub repository")
        event_box.connect("button-press-event", self._open_project_repository)
        event_box.add(box)

        self.content_widget = event_box
        self.pack_start(event_box, True, True, 0)

    def _open_project_repository(self, *_args):
        try:
            Gtk.show_uri_on_window(None, self.github_url, Gtk.get_current_event_time())
        except Exception:
            return False
        return True

    def _on_size_allocate(self, widget, allocation):
        try:
            self._set_render_size(allocation.width, allocation.height)
        except Exception:
            self._show_fallback()

    def _fit_size_for_allocation(self, width, height):
        available_width = max(1, int(width))
        available_height = max(1, int(height))
        if self._source_pixbuf is None:
            return 1, 1
        source_width = max(1, self._source_pixbuf.get_width())
        source_height = max(1, self._source_pixbuf.get_height())
        max_width = max(1, int(self.max_width))
        max_height = max(1, int(self.max_height))
        scale = min(
            float(available_width) / float(source_width),
            float(available_height) / float(source_height),
            float(max_width) / float(source_width),
            float(max_height) / float(source_height),
        )
        if scale <= 0:
            return 1, 1
        return (
            max(1, int(round(source_width * scale))),
            max(1, int(round(source_height * scale))),
        )

    def _set_render_size(self, width, height):
        if self._source_pixbuf is None or self._drawing_area is None:
            return
        target_width, target_height = self._fit_size_for_allocation(width, height)
        current = (target_width, target_height)
        if current == self._last_render_size and self._scaled_pixbuf is not None:
            return
        self._last_render_size = current
        try:
            scaled = self._source_pixbuf.scale_simple(
                target_width,
                target_height,
                GdkPixbuf.InterpType.BILINEAR,
            )
            if scaled is None:
                raise RuntimeError("logo scaling returned no pixbuf")
            self._scaled_pixbuf = scaled
        except Exception:
            self._show_fallback()
            return
        self._drawing_area.queue_draw()

    def _show_fallback(self):
        self._source_pixbuf = None
        self._scaled_pixbuf = None
        if self._drawing_area is not None:
            try:
                self._drawing_area.hide()
            except Exception:
                pass
        self._drawing_area = None
        if self._logo_box is None or self._fallback_label is not None:
            return
        fallback = Gtk.Label(label=self._fallback_text)
        fallback.set_halign(Gtk.Align.CENTER)
        fallback.set_hexpand(True)
        self._fallback_label = fallback
        self._logo_box.pack_start(fallback, True, True, 0)
        fallback.show()

    def _on_draw(self, widget, cr):
        if self._source_pixbuf is None:
            return False
        try:
            allocation = widget.get_allocation()
            self._set_render_size(allocation.width, allocation.height)
            if self._scaled_pixbuf is None:
                return False
            x_offset = max(0, int((allocation.width - self._scaled_pixbuf.get_width()) / 2))
            y_offset = max(0, int((allocation.height - self._scaled_pixbuf.get_height()) / 2))
            Gdk.cairo_set_source_pixbuf(cr, self._scaled_pixbuf, x_offset, y_offset)
            cr.paint()
        except Exception:
            self._show_fallback()
        return False


class HeaderLogo(_ResponsiveLogo):
    asset_name = "settings-header-logo.png"
    top_margin = 0
    bottom_margin = 18


class FooterLogo(_ResponsiveLogo):
    asset_name = "settings-footer-logo.png"
    top_margin = 18
    bottom_margin = 0


def _remove_settings_listener(settings, target, callback):
    if settings is None or not target or callback is None:
        return
    listeners = getattr(settings, "listeners", None)
    if not isinstance(listeners, dict):
        return
    callbacks = listeners.get(target)
    if not isinstance(callbacks, list):
        return
    try:
        callbacks.remove(callback)
    except ValueError:
        return
    if not callbacks:
        listeners.pop(target, None)


class StatusIconSelector(SettingsWidget):
    allowed_targets = (
        "status-icon-ready",
        "status-icon-recording",
        "status-icon-processing",
        "status-icon-recorded",
        "status-icon-error",
        "status-icon-setup",
    )
    target_families = {
        "status-icon-ready": "ready",
        "status-icon-recording": "recording",
        "status-icon-processing": "processing",
        "status-icon-recorded": "recorded",
        "status-icon-error": "error",
        "status-icon-setup": "setup",
    }
    icon_sets = (
        ("original", "Original Speed of Cinnamon", 0, 0),
        ("classic", "Classic green SOC", 46, 51),
        ("alternatives", "Alternatives", 1, 30),
        ("mouse", "Mice", 31, 35),
        ("owl", "Owls", 36, 40),
        ("moon", "Moons", 41, 45),
    )
    classic_labels = ("SOC", "SOC.", "SOC..", "SOC...", "Status", "Microphone")

    def __init__(self, info, key, settings):
        SettingsWidget.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_spacing(6)
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_hexpand(True)

        target = info.get("target", key)
        self._target = target if isinstance(target, str) and target in self.allowed_targets else ""
        self._family = self.target_families.get(self._target, "")
        self._settings = settings
        self._updating = False
        self._destroyed = False
        self._settings_listener = None
        self._set_combo = Gtk.ComboBoxText()
        self._icon_combo = Gtk.ComboBoxText()

        title = Gtk.Label(label=str(info.get("description", "")))
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        tooltip = info.get("tooltip", "")
        if isinstance(tooltip, str) and tooltip:
            title.set_tooltip_text(tooltip)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(6)
        grid.set_hexpand(True)
        set_label = Gtk.Label(label="Icon set")
        set_label.set_halign(Gtk.Align.START)
        icon_label = Gtk.Label(label="Icon")
        icon_label.set_halign(Gtk.Align.START)
        self._set_combo.set_hexpand(True)
        self._icon_combo.set_hexpand(True)
        grid.attach(set_label, 0, 0, 1, 1)
        grid.attach(self._set_combo, 1, 0, 1, 1)
        grid.attach(icon_label, 0, 1, 1, 1)
        grid.attach(self._icon_combo, 1, 1, 1, 1)

        for set_id, label, _first, _last in self.icon_sets:
            self._set_combo.append(set_id, label)
        self._set_combo.connect("changed", self._on_set_changed)
        self._icon_combo.connect("changed", self._on_icon_changed)

        self.content_widget = grid
        self.pack_start(title, False, False, 0)
        self.pack_start(grid, False, False, 0)
        self.connect("destroy", self._on_destroy)
        self._load_from_settings()
        if self._target:
            try:
                widget_ref = weakref.ref(self)

                def settings_listener(*args):
                    widget = widget_ref()
                    if widget is not None and not widget._destroyed:
                        widget._load_from_settings()

                self._settings_listener = settings_listener
                self._settings.listen(self._target, settings_listener)
            except Exception:
                pass

    def _on_destroy(self, *_args):
        self._destroyed = True
        _remove_settings_listener(self._settings, self._target, self._settings_listener)
        self._settings = None
        self._settings_listener = None
        self._set_combo = None
        self._icon_combo = None

    def _read_setting(self):
        if not self._settings or not self._target:
            return "soc-original"
        try:
            return self._settings.get_value(self._target)
        except Exception:
            return "soc-original"

    def _slot_from_value(self, value):
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if candidate == "soc-original":
            return 0
        prefix, separator, suffix = candidate.rpartition("-")
        if separator != "-" or prefix not in self.target_families.values():
            return None
        if len(suffix) != 2 or not suffix.isdigit():
            return None
        slot = int(suffix)
        return slot if 1 <= slot <= 51 else None

    def _normalize_for_target(self, value):
        slot = self._slot_from_value(value)
        if slot == 0:
            return "soc-original"
        if slot is None or not self._family:
            return "soc-original"
        candidate = f"{self._family}-{slot:02d}"
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidate_path = os.path.join(base_dir, "assets", "status-icons", f"{candidate}.png")
        return candidate if os.path.isfile(candidate_path) else "soc-original"

    def _set_id_for_slot(self, slot):
        if slot == 0:
            return "original"
        for set_id, _label, first, last in self.icon_sets:
            if first <= slot <= last and first != 0:
                return set_id
        return "original"

    def _options_for_set(self, set_id):
        if set_id == "original":
            return (("soc-original", "Original Speed of Cinnamon"),)
        for candidate_id, _label, first, last in self.icon_sets:
            if candidate_id != set_id:
                continue
            options = []
            for slot in range(first, last + 1):
                icon_id = f"{self._family}-{slot:02d}"
                if set_id == "alternatives":
                    label = f"Alternative {slot}"
                elif set_id == "classic":
                    label = self.classic_labels[slot - first]
                else:
                    label = f"{set_id.title()} {slot - first + 1}"
                options.append((icon_id, label))
            return tuple(options)
        return (("soc-original", "Original Speed of Cinnamon"),)

    def _populate_icons(self, set_id, preferred_icon):
        self._icon_combo.remove_all()
        options = self._options_for_set(set_id)
        option_ids = []
        for icon_id, label in options:
            option_ids.append(icon_id)
            self._icon_combo.append(icon_id, label)
        active_id = preferred_icon if preferred_icon in option_ids else option_ids[0]
        self._icon_combo.set_active_id(active_id)
        return active_id

    def _load_from_settings(self):
        if self._destroyed or self._set_combo is None or self._icon_combo is None:
            return
        raw_value = self._read_setting()
        normalized = self._normalize_for_target(raw_value)
        slot = self._slot_from_value(normalized)
        set_id = self._set_id_for_slot(slot if slot is not None else 0)
        self._updating = True
        try:
            self._set_combo.set_active_id(set_id)
            self._populate_icons(set_id, normalized)
            if normalized != raw_value and self._settings and self._target:
                self._settings.set_value(self._target, normalized)
        finally:
            self._updating = False

    def _write_icon(self, icon_id):
        normalized = self._normalize_for_target(icon_id)
        if self._settings and self._target:
            self._settings.set_value(self._target, normalized)

    def _on_set_changed(self, *_args):
        if self._updating or self._set_combo is None or self._icon_combo is None:
            return
        set_id = self._set_combo.get_active_id() or "original"
        self._updating = True
        try:
            active_id = self._populate_icons(set_id, "")
            self._write_icon(active_id)
        finally:
            self._updating = False

    def _on_icon_changed(self, *_args):
        if self._updating or self._icon_combo is None:
            return
        icon_id = self._icon_combo.get_active_id()
        if icon_id:
            self._write_icon(icon_id)


class StatusIconPreview(SettingsWidget):
    max_size = 112
    top_margin = 10
    bottom_margin = 10
    allowed_targets = (
        "status-icon-ready",
        "status-icon-recording",
        "status-icon-processing",
        "status-icon-recorded",
        "status-icon-error",
        "status-icon-setup",
    )

    def __init__(self, info, key, settings):
        SettingsWidget.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_spacing(0)
        self.set_margin_top(self.top_margin)
        self.set_margin_bottom(self.bottom_margin)
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)

        self._settings = settings
        target = info.get("target", "")
        self._target = target if isinstance(target, str) and target in self.allowed_targets else ""
        self._fallback_text = info.get("description", "")
        self._drawing_area = None
        self._fallback_label = None
        self._source_pixbuf = None
        self._scaled_pixbuf = None
        self._last_render_size = (0, 0)
        self._last_icon_id = None
        self._destroyed = False
        self._settings_listener = None

        base_dir = os.path.dirname(os.path.abspath(__file__))
        self._asset_base_dir = os.path.join(base_dir, "assets", "status-icons")
        allowed_status_ids = []
        for state in ("ready", "recording", "processing", "recorded", "error", "setup"):
            for idx in range(1, 52):
                allowed_status_ids.append(
                    "{}-{}{}".format(state, "0" if idx < 10 else "", idx)
                )
        self._allowed_status_ids = ("soc-original",) + tuple(allowed_status_ids)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.set_halign(Gtk.Align.FILL)
        box.set_hexpand(True)
        self._preview_box = box

        try:
            self._drawing_area = Gtk.DrawingArea()
            self._drawing_area.set_halign(Gtk.Align.FILL)
            self._drawing_area.set_valign(Gtk.Align.CENTER)
            self._drawing_area.set_hexpand(True)
            self._drawing_area.set_size_request(1, self.max_size)
            self._drawing_area.connect("draw", self._on_draw)
            box.pack_start(self._drawing_area, True, True, 0)
            box.connect("size-allocate", self._on_size_allocate)
            self.content_widget = box
            self.pack_start(box, True, True, 0)
            self.connect("destroy", self._on_destroy)
            self._load_from_settings()
            if self._target:
                try:
                    widget_ref = weakref.ref(self)

                    def settings_listener(*args):
                        widget = widget_ref()
                        if widget is not None and not widget._destroyed:
                            widget._on_target_change(*args)

                    self._settings_listener = settings_listener
                    self._settings.listen(self._target, settings_listener)
                except Exception:
                    pass
        except Exception:
            self._show_fallback()

    def _on_target_change(self, *_args):
        if self._destroyed:
            return
        self._load_from_settings()

    def _on_destroy(self, *_args):
        self._destroyed = True
        _remove_settings_listener(self._settings, self._target, self._settings_listener)
        self._settings = None
        self._settings_listener = None
        self._source_pixbuf = None
        self._scaled_pixbuf = None
        self._drawing_area = None
        self._fallback_label = None

    def _load_from_settings(self):
        icon_id = self._normalize_icon_id(self._read_setting())
        if icon_id is None:
            self._show_fallback()
            return
        if icon_id == self._last_icon_id:
            return
        self._load_icon(icon_id)

    def _read_setting(self):
        if not self._settings or not self._target:
            return None
        try:
            value = self._settings.get_value(self._target)
        except Exception:
            return None
        return value

    def _normalize_icon_id(self, value):
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if candidate in self._allowed_status_ids:
            return candidate
        return None

    def _load_icon(self, icon_id):
        if icon_id not in self._allowed_status_ids:
            self._show_fallback()
            return
        try:
            if icon_id == "soc-original":
                icon_name = "audio-input-microphone-symbolic"
                if self._target == "status-icon-recording":
                    icon_name = "media-record-symbolic"
                elif self._target == "status-icon-processing":
                    icon_name = "view-refresh-symbolic"
                source_pixbuf = Gtk.IconTheme.get_default().load_icon(
                    icon_name,
                    self.max_size,
                    Gtk.IconLookupFlags.FORCE_SIZE,
                )
            else:
                logo_path = os.path.join(self._asset_base_dir, f"{icon_id}.png")
                source_pixbuf = GdkPixbuf.Pixbuf.new_from_file(logo_path)
        except Exception:
            self._show_fallback()
            return

        self._source_pixbuf = source_pixbuf
        self._last_icon_id = icon_id
        self._scaled_pixbuf = None
        self._last_render_size = (0, 0)
        if self._fallback_label is not None:
            self._fallback_label.hide()
        if self._drawing_area is not None:
            self._drawing_area.show()
            self._drawing_area.queue_draw()
        if self._drawing_area is not None:
            allocation = self._drawing_area.get_allocation()
            self._on_size_allocate(self._drawing_area, allocation)

    def _fit_size_for_allocation(self, width, height):
        if self._source_pixbuf is None:
            return 1, 1

        available_width = max(1, int(width))
        available_height = max(1, int(height))
        max_target = max(1, int(self.max_size))
        available_width = min(available_width, max_target)
        available_height = min(available_height, max_target)

        source_width = max(1, self._source_pixbuf.get_width())
        source_height = max(1, self._source_pixbuf.get_height())
        scale = min(
            float(available_width) / float(source_width),
            float(available_height) / float(source_height),
        )
        if scale <= 0:
            return 1, 1

        target_width = max(1, int(round(source_width * scale)))
        target_height = max(1, int(round(source_height * scale)))
        return target_width, target_height

    def _on_size_allocate(self, _widget, allocation):
        if allocation.width <= 1 or allocation.height <= 1:
            return
        self._set_render_size(allocation.width, allocation.height)

    def _set_render_size(self, width, height):
        if self._source_pixbuf is None or self._drawing_area is None:
            return
        target_width, target_height = self._fit_size_for_allocation(width, height)
        current_size = (target_width, target_height)
        if current_size == self._last_render_size and self._scaled_pixbuf is not None:
            return
        self._last_render_size = current_size
        try:
            scaled = self._source_pixbuf.scale_simple(
                target_width,
                target_height,
                GdkPixbuf.InterpType.BILINEAR,
            )
            if scaled is None:
                raise RuntimeError("status icon scaling returned no pixbuf")
            self._scaled_pixbuf = scaled
        except Exception:
            self._show_fallback()
            return
        self._drawing_area.queue_draw()

    def _show_fallback(self):
        self._source_pixbuf = None
        self._scaled_pixbuf = None
        self._last_icon_id = None
        self._last_render_size = (0, 0)
        if self._drawing_area is not None:
            try:
                self._drawing_area.hide()
            except Exception:
                pass
        if self._fallback_label is not None:
            self._fallback_label.show()
            return
        fallback = Gtk.Label(label=self._fallback_text)
        fallback.set_halign(Gtk.Align.CENTER)
        fallback.set_hexpand(True)
        self._preview_box.pack_start(fallback, True, True, 0)
        fallback.show()
        self._fallback_label = fallback

    def _on_draw(self, widget, cr):
        if self._source_pixbuf is None:
            return False
        try:
            if self._scaled_pixbuf is None:
                return False

            allocation = widget.get_allocation()

            x_offset = max(0, int((allocation.width - self._scaled_pixbuf.get_width()) / 2))
            y_offset = max(0, int((allocation.height - self._scaled_pixbuf.get_height()) / 2))
            Gdk.cairo_set_source_pixbuf(cr, self._scaled_pixbuf, x_offset, y_offset)
            cr.paint()
            return False
        except Exception:
            self._show_fallback()
            return False
