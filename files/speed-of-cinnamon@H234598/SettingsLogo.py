#!/usr/bin/python3
# pylint: disable=import-error

import os

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
            self._source_pixbuf = GdkPixbuf.Pixbuf.new_from_file(logo_path)
            self._drawing_area = Gtk.DrawingArea()
            self._drawing_area.set_halign(Gtk.Align.FILL)
            self._drawing_area.set_hexpand(True)
            self._drawing_area.set_size_request(1, 1)
            self._drawing_area.connect("draw", self._on_draw)
            box.pack_start(self._drawing_area, True, True, 0)
            box.connect("size-allocate", self._on_size_allocate)
            self._set_render_width(self.max_width)
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
            self._set_render_width(allocation.width)
        except Exception:
            self._show_fallback()

    def _fit_size_for_width(self, width):
        target_width = max(1, int(width))
        if self._source_pixbuf is None:
            return 1, 1
        source_width = max(1, self._source_pixbuf.get_width())
        source_height = max(1, self._source_pixbuf.get_height())
        target_width = min(target_width, max(1, int(self.max_width)))
        height_limited_width = max(1, int(self.max_height * source_width / source_height))
        target_width = min(target_width, height_limited_width)
        target_height = max(1, min(int(self.max_height), int(round(target_width * source_height / source_width))))
        return target_width, target_height

    def _set_render_width(self, width):
        if self._source_pixbuf is None or self._drawing_area is None:
            return
        target_width, target_height = self._fit_size_for_width(width)
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
        self._drawing_area.set_size_request(1, target_height)
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
            self._set_render_width(allocation.width)
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


class StatusIconPreview(SettingsWidget):
    max_size = 112
    top_margin = 10
    bottom_margin = 10

    def __init__(self, info, key, settings):
        SettingsWidget.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_spacing(0)
        self.set_margin_top(self.top_margin)
        self.set_margin_bottom(self.bottom_margin)
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)

        self._settings = settings
        self._target = info.get("target", "")
        self._fallback_text = info.get("description", "")
        self._drawing_area = None
        self._fallback_label = None
        self._source_pixbuf = None
        self._scaled_pixbuf = None
        self._last_render_size = (0, 0)
        self._last_icon_id = None

        base_dir = os.path.dirname(os.path.abspath(__file__))
        self._asset_base_dir = os.path.join(base_dir, "assets", "status-icons")
        allowed_status_ids = []
        for state in ("ready", "recording", "processing"):
            for idx in range(1, 46):
                allowed_status_ids.append(
                    "{}-{}{}".format(state, "0" if idx < 10 else "", idx)
                )
        self._allowed_status_ids = ("soc-original",) + tuple(allowed_status_ids)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.set_halign(Gtk.Align.FILL)
        box.set_hexpand(True)

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
            self._load_from_settings()
            if self._target:
                try:
                    self._settings.listen(self._target, self._on_target_change)
                except Exception:
                    pass
        except Exception:
            self._show_fallback()

    def _on_target_change(self, *_args):
        self._load_from_settings()

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
        self._drawing_area.set_size_request(1, target_height)
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
        self.pack_start(fallback, True, True, 0)
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
