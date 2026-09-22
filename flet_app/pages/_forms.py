"""Shared write-form plumbing for the editable pages.

Every page that writes to the server needs the same four things: a status
line, a way to call a write and report its result, a re-read afterwards, and
a text field that submits on Enter. Without this they each grow their own
slightly different version and drift.

The rule these encode: **nothing changes on screen until the server confirms
it.** Optimistic UI is tempting on a phone, but the same records are edited
from the website, and a row that updates locally without ever reaching the
server is worse than a visible failure - it looks like it worked.
"""
import flet as ft

from ..ui import PAL, PAD, FOCUS_WIDTH


class Writer:
    """Status line + handlers that call a write, report, and refresh."""

    def __init__(self, page, rebuild):
        p = PAL()
        self.page = page
        self.rebuild = rebuild
        self.text = ft.Text("", size=11, color=p["text2"], selectable=True)
        self._p = p

    @property
    def control(self):
        return ft.Container(padding=PAD(0, 4), content=self.text)

    def report(self, ok, msg):
        self.text.value = msg if ok else f"not saved - {msg}"
        self.text.color = self._p["text2"] if ok else self._p["pink"]

    def busy(self):
        self.text.value = "saving..."
        self.text.color = self._p["text2"]
        self.page.update()

    def run(self, fn, *args):
        """on_click handler: call fn(*args), report, refresh only on success."""
        async def handler(e):
            self.busy()
            ok, msg = fn(*args)
            self.report(ok, msg)
            if ok:
                self.rebuild()
            else:
                self.page.update()
        return handler

    def entry(self, hint, fn, *, multiline=False, width=None, icon=ft.Icons.ADD):
        """A text field that submits on Enter and clears only when the write lands.

        `fn` takes the entered string and returns (ok, message).
        """
        p = self._p
        field = ft.TextField(
            hint_text=hint, dense=True, filled=True, text_size=12,
            expand=width is None, width=width, multiline=multiline,
            min_lines=4 if multiline else 1, max_lines=12 if multiline else 1,
            border_color=p["border"], focused_border_color=p["accent"],
            # Every add-form in the app comes through here, so this is where
            # the accent glow reaches a focused field. See ui.FOCUS_WIDTH().
            focused_border_width=FOCUS_WIDTH(),
            color=p["text"])

        async def submit(e):
            value = (field.value or "").strip()
            if not value:
                return
            self.busy()
            ok, msg = fn(value)
            self.report(ok, msg)
            if ok:
                field.value = ""
                self.rebuild()
            else:
                self.page.update()

        if not multiline:
            field.on_submit = submit
        return ft.Row(spacing=6, controls=[
            field, ft.IconButton(icon, icon_color=p["accent"], on_click=submit)])


def rebuilder(page, build_fn):
    """Re-read from the server and repaint the whole page."""
    from .. import api as M
    from ..ui import S

    def rebuild():
        M.refresh()
        # Prefer the whole-app re-render main.py registers on S. Replacing
        # page.controls directly, as this used to, threw away the topbar and
        # the navigation bar along with the page body - after any write you
        # were stranded on a page with no way off it.
        rerender = getattr(S, "rerender", None)
        if callable(rerender):
            rerender()
            return
        page.controls.clear()      # standalone/tests: no chrome to preserve
        page.add(build_fn(page))
        page.update()
    return rebuild


def offline_banner(p, what="this page", sample=True):
    """The "you are offline" strip.

    `sample=False` for a page that shows nothing rather than sample data
    when it cannot reach the server. Several now do - the claim that sample
    data is on screen is itself a small untruth when the list below the
    banner is honestly empty, and this app cannot afford those.
    """
    from ..ui import card
    lead = ("Showing sample data - not connected to the server."
            if sample else "Not connected to the server.")
    return card(ft.Row(spacing=8, controls=[
        ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=p["yellow"]),
        ft.Text(f"{lead} Changes to {what} will not save.", size=11,
                color=p["text2"], expand=True)]), pad=10)
