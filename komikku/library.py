# Copyright (C) 2019-2020 Valéry Febvre
# SPDX-License-Identifier: GPL-3.0-only or GPL-3.0-or-later
# Author: Valéry Febvre <vfebvre@easter-eggs.com>

import cairo
from gettext import gettext as _
import time

from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import Gtk
from gi.repository.GdkPixbuf import InterpType
from gi.repository.GdkPixbuf import Pixbuf

from komikku.downloader import DownloadManagerDialog
from komikku.models import create_db_connection
from komikku.models import Manga


class Library():
    selection_mode = False
    selection_count = 0

    def __init__(self, window):
        self.window = window
        self.builder = window.builder
        self.builder.add_from_resource('/info/febvre/Komikku/ui/menu/library_selection_mode.xml')

        # Search
        self.title_stack = self.builder.get_object('library_page_title_stack')
        self.search_entry = self.builder.get_object('library_page_search_searchentry')
        self.search_entry.connect('changed', self.search)
        self.search_button = self.builder.get_object('library_page_search_button')
        self.search_button.connect('clicked', self.toggle_search_entry)

        self.flowbox = self.builder.get_object('library_page_flowbox')
        self.flowbox.connect('child-activated', self.on_manga_clicked)
        self.gesture = Gtk.GestureLongPress.new(self.flowbox)
        self.gesture.set_touch_only(False)
        self.gesture.connect('pressed', self.enter_selection_mode)

        def _filter(child):
            manga = Manga.get(child.get_children()[0].manga.id)
            term = self.search_entry.get_text().lower()
            return term in manga.name.lower() or term in manga.server.name.lower()

        def _sort(child1, child2):
            """
            This function gets two children and has to return:
            - a negative integer if the firstone should come before the second one
            - zero if they are equal
            - a positive integer if the second one should come before the firstone
            """
            manga1 = Manga.get(child1.get_children()[0].manga.id)
            manga2 = Manga.get(child2.get_children()[0].manga.id)

            if manga1.last_read > manga2.last_read:
                return -1

            if manga1.last_read < manga2.last_read:
                return 1

            return 0

        self.populate()

        self.flowbox.set_filter_func(_filter)
        self.flowbox.set_sort_func(_sort)

    @property
    def cover_size(self):
        default_width = 180
        default_height = 250

        box_width = self.window.get_size().width
        # Padding of flowbox children is 4px
        # https://pastebin.com/Q4ahCcgu
        padding = 4
        child_width = default_width + padding * 2
        if box_width / child_width != box_width // child_width:
            nb = box_width // child_width + 1
            width = box_width // nb - (padding * 2)
            height = default_height // (default_width / width)
        else:
            width = default_width
            height = default_height

        return width, height

    def add_actions(self):
        # Menu actions
        update_action = Gio.SimpleAction.new('library.update', None)
        update_action.connect('activate', self.update_all)
        self.window.application.add_action(update_action)

        download_manager_action = Gio.SimpleAction.new('library.download-manager', None)
        download_manager_action.connect('activate', self.open_download_manager)
        self.window.application.add_action(download_manager_action)

        # Menu actions in selection mode
        delete_selected_action = Gio.SimpleAction.new('library.delete-selected', None)
        delete_selected_action.connect('activate', self.delete_selected)
        self.window.application.add_action(delete_selected_action)

        update_selected_action = Gio.SimpleAction.new('library.update-selected', None)
        update_selected_action.connect('activate', self.update_selected)
        self.window.application.add_action(update_selected_action)

        select_all_action = Gio.SimpleAction.new('library.select-all', None)
        select_all_action.connect('activate', self.select_all)
        self.window.application.add_action(select_all_action)

    def add_manga(self, manga, position=-1):
        width, height = self.cover_size

        overlay = Gtk.Overlay()
        overlay.set_halign(Gtk.Align.CENTER)
        overlay.set_valign(Gtk.Align.CENTER)
        overlay.manga = manga
        overlay._pixbuf = None
        overlay._selected = False

        # Cover
        overlay.add_overlay(Gtk.Image())
        self.set_manga_cover_image(overlay, width, height)

        # Name (bottom)
        label = Gtk.Label(xalign=0)
        label.get_style_context().add_class('library-manga-name-label')
        label.set_valign(Gtk.Align.END)
        label.set_line_wrap(True)
        label.set_text(manga.name)
        overlay.add_overlay(label)

        # Server logo (top left corner)
        drawingarea = Gtk.DrawingArea()
        drawingarea.connect('draw', self.draw_cover_server_logo, manga)
        overlay.add_overlay(drawingarea)

        # Badges: number of recents chapters and number of downloaded chapters (top right corner)
        drawingarea = Gtk.DrawingArea()
        drawingarea.connect('draw', self.draw_cover_badges, manga)
        overlay.add_overlay(drawingarea)

        overlay.show_all()
        self.flowbox.insert(overlay, position)

    def delete_selected(self, action, param):
        def confirm_callback():
            # Stop Downloader & Updater
            self.window.downloader.stop()
            self.window.updater.stop()

            while self.window.downloader.running or self.window.updater.running:
                time.sleep(0.1)
                continue

            # Safely delete mangas in DB
            for child in self.flowbox.get_selected_children():
                manga = child.get_children()[0].manga
                manga.delete()

            # Restart Downloader & Updater
            self.window.downloader.start()
            self.window.updater.start()

            # Finally, update library
            self.populate()

            self.leave_selection_mode()

        self.window.confirm(
            _('Delete?'),
            _('Are you sure you want to delete selected mangas?'),
            confirm_callback
        )

    def draw_cover_badges(self, da, ctx, manga):
        """
        Draws badges in top right corner of cover
        * Unread chapter: green
        * Recent chapters: blue
        * Downloaded chapters: red
        """
        nb_unread_chapters = manga.nb_unread_chapters
        nb_recent_chapters = manga.nb_recent_chapters
        nb_downloaded_chapters = manga.nb_downloaded_chapters

        if nb_unread_chapters == nb_recent_chapters == nb_downloaded_chapters == 0:
            return

        cover_width, _cover_height = self.cover_size
        spacing = 5  # with top and right borders, between badges
        x = cover_width

        ctx.save()
        ctx.set_font_size(13)

        def draw_badge(nb, color_r, color_g, color_b):
            nonlocal x

            if nb == 0:
                return

            text = str(nb)
            text_extents = ctx.text_extents(text)
            width = text_extents.x_advance + 2 * 3 + 1
            height = text_extents.height + 2 * 5

            # Draw rectangle
            x = x - spacing - width
            ctx.set_source_rgb(color_r, color_g, color_b)
            ctx.rectangle(x, spacing, width, height)
            ctx.fill()

            # Draw number
            ctx.set_source_rgb(1, 1, 1)
            ctx.move_to(x + 3, height)
            ctx.show_text(text)

        draw_badge(nb_unread_chapters, 0.2, 0.5, 0)        # #338000
        draw_badge(nb_recent_chapters, 0.2, 0.6, 1)        # #3399FF
        draw_badge(nb_downloaded_chapters, 1, 0.266, 0.2)  # #FF4433

        ctx.restore()

    @staticmethod
    def draw_cover_server_logo(da, ctx, manga):
        size = 75

        ctx.save()

        # Draw triangle
        gradient = cairo.LinearGradient(0, 0, size / 2, size / 2)
        gradient.add_color_stop_rgba(0, 0, 0, 0, 0.75)
        gradient.add_color_stop_rgba(1, 0, 0, 0, 0)
        ctx.set_source(gradient)
        ctx.new_path()
        ctx.move_to(0, 0)
        ctx.rel_line_to(0, size)
        ctx.rel_line_to(size, -size)
        ctx.close_path()
        ctx.fill()

        # Draw server logo
        pixbuf = Pixbuf.new_from_resource_at_scale(manga.server.logo_resource_path, 20, 20, True)
        Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 4, 4)
        ctx.paint()

        ctx.restore()

    def enter_selection_mode(self, gesture, x, y):
        self.selection_mode = True
        self.selection_count = 1

        self.flowbox.set_selection_mode(Gtk.SelectionMode.MULTIPLE)

        selected_child = self.flowbox.get_child_at_pos(x, y)
        selected_overlay = selected_child.get_children()[0]
        self.flowbox.select_child(selected_child)
        selected_overlay._selected = True

        self.window.titlebar.set_selection_mode(True)
        self.window.left_button_image.set_from_icon_name('go-previous-symbolic', Gtk.IconSize.MENU)
        self.window.menu_button.set_menu_model(self.builder.get_object('menu-library-selection-mode'))

    def leave_selection_mode(self):
        self.selection_mode = False

        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        for child in self.flowbox.get_children():
            overlay = child.get_children()[0]
            overlay._selected = False

        self.window.titlebar.set_selection_mode(False)
        self.window.left_button_image.set_from_icon_name('list-add-symbolic', Gtk.IconSize.MENU)
        self.window.menu_button.set_menu_model(self.builder.get_object('menu'))

    def on_manga_added(self, manga):
        """
        Called from 'Add dialog' when user clicks on [+] button
        """
        db_conn = create_db_connection()
        nb_mangas = db_conn.execute('SELECT count(*) FROM mangas').fetchone()[0]
        db_conn.close()

        if nb_mangas == 1:
            # Library was previously empty
            self.populate()
        else:
            self.add_manga(manga, position=0)

    def on_manga_clicked(self, flowbox, child):
        if self.selection_mode:
            overlay = child.get_children()[0]
            if overlay._selected:
                self.selection_count -= 1
                self.flowbox.unselect_child(child)
                overlay._selected = False
            else:
                self.selection_count += 1
                overlay._selected = True
            if self.selection_count == 0:
                self.leave_selection_mode()
        else:
            self.window.card.init(child.get_children()[0].manga)

    def on_manga_deleted(self, manga):
        # Remove manga cover in flowbox
        for child in self.flowbox.get_children():
            if child.get_children()[0].manga.id == manga.id:
                child.destroy()
                break

    def on_resize(self):
        if self.window.first_start_grid.is_ancestor(self.window):
            return

        width, height = self.cover_size

        for child in self.flowbox.get_children():
            overlay = child.get_children()[0]
            self.set_manga_cover_image(overlay, width, height)

    def open_download_manager(self, action, param):
        DownloadManagerDialog(self.window).open(action, param)

    def populate(self):
        db_conn = create_db_connection()
        mangas_rows = db_conn.execute('SELECT * FROM mangas ORDER BY last_read DESC').fetchall()

        if len(mangas_rows) == 0:
            if self.window.overlay.is_ancestor(self.window):
                self.window.remove(self.window.overlay)

            # Display first start message
            self.window.add(self.window.first_start_grid)

            return

        if self.window.first_start_grid.is_ancestor(self.window):
            self.window.remove(self.window.first_start_grid)

        if not self.window.overlay.is_ancestor(self.window):
            self.window.add(self.window.overlay)

        # Clear library flowbox
        for child in self.flowbox.get_children():
            child.destroy()

        # Populate flowbox with mangas
        for row in mangas_rows:
            self.add_manga(Manga.get(row['id']))

        db_conn.close()

    def search(self, search_entry):
        self.flowbox.invalidate_filter()

    def select_all(self, action, param):
        self.selection_count = 0

        for child in self.flowbox.get_children():
            overlay = child.get_children()[0]
            overlay._selected = True
            self.flowbox.select_child(child)
            self.selection_count += 1

    @staticmethod
    def set_manga_cover_image(overlay, width, height):
        overlay.set_size_request(width, height)

        if overlay._pixbuf is None:
            manga = overlay.manga
            if manga.cover_fs_path is not None:
                overlay._pixbuf = Pixbuf.new_from_file(manga.cover_fs_path)
            else:
                overlay._pixbuf = Pixbuf.new_from_resource('/info/febvre/Komikku/images/missing_file.png')

        pixbuf = overlay._pixbuf.scale_simple(width, height, InterpType.BILINEAR)
        image = overlay.get_children()[0]
        image.set_from_pixbuf(pixbuf)

    def show(self, invalidate_sort=False):
        self.window.left_button_image.set_from_icon_name('list-add-symbolic', Gtk.IconSize.MENU)

        self.builder.get_object('fullscreen_button').hide()

        self.window.menu_button.set_menu_model(self.builder.get_object('menu'))
        self.window.menu_button_image.set_from_icon_name('open-menu-symbolic', Gtk.IconSize.MENU)

        if invalidate_sort:
            self.flowbox.invalidate_sort()

        self.window.show_page('library')

    def toggle_search_entry(self, button):
        if button.get_active():
            self.title_stack.set_visible_child_name('searchentry')
            self.search_entry.grab_focus()
        else:
            self.title_stack.set_visible_child_name('title')
            self.search_entry.set_text('')
            self.search_entry.grab_remove()

    def update_all(self, action, param):
        self.window.updater.update_library()

    def update_selected(self, action, param):
        self.window.updater.add([child.get_children()[0].manga for child in self.flowbox.get_selected_children()])
        self.window.updater.start()

        self.leave_selection_mode()
