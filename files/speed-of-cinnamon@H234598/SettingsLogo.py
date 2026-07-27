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
