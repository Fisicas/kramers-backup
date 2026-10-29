"""
fastui.py -- flat, image-free styles for the large widgets under Sun Valley.

Sun Valley (sv-ttk) draws widgets by tiling small translucent PNG slices.
Tk on Windows blends translucent images in software, slice by slice, so a
wide Entry, a full-width progress bar or a table header costs hundreds of
blends on every repaint, and resizing the window crawled (about 1.4 s per
step). These styles keep Sun Valley's look for small widgets (buttons,
checkboxes) and draw the big, stretched ones with clam's flat, vector
elements in Sun Valley's colours.

    Flat.Treeview (+ .Heading)   Flat.TEntry   Flat.TCombobox
    Flat.Horizontal.TProgressbar Flat.Vertical.TScrollbar   Flat.Horizontal.TScrollbar
    Flat.TButton   Flat.Accent.TButton
"""
from __future__ import annotations

from tkinter import TclError, ttk

PALETTES = {
    True: dict(bg="#1c1c1c", field="#2b2b2b", border="#3d3d3d", fg="#fafafa", accent="#57c8ff",
               heading="#262626", thumb="#4a4a4a", thumb_hot="#666666", sel="#1f4d68",
               trough="#2b2b2b", heading_hot="#353535", field_off="#232323", fg_off="#7a7a7a",
               accent_hot="#4cb3e6", accent_down="#3f9ccb", accent_fg="#000000"),
    False: dict(bg="#fafafa", field="#ffffff", border="#d0d0d0", fg="#1c1c1c", accent="#005fb8",
                heading="#f3f3f3", thumb="#a8a8a8", thumb_hot="#8a8a8a", sel="#cce4f7",
                trough="#e6e6e6", heading_hot="#ececec", field_off="#f5f5f5", fg_off="#a0a0a0",
                accent_hot="#196ebf", accent_down="#2a7cc7", accent_fg="#ffffff"),
}

_ELEMENTS = (("Flat.field", "clam", "field"),
             ("Flat.Combobox.field", "clam", "Combobox.field"),
             ("Flat.Combobox.downarrow", "clam", "Combobox.downarrow"),
             ("Flat.trough", "clam", "trough"),
             ("Flat.pbar", "clam", "pbar"),
             ("Flat.thumb", "clam", "thumb"),
             ("Flat.Treeheading.cell", "default", "Treeheading.cell"),
             ("Flat.Button.border", "clam", "border"))


def install(root, dark: bool) -> bool:
    """Create the Flat.* styles in the current theme. False if not possible."""
    st = ttk.Style(root)
    p = PALETTES[dark]
    try:
        for name, theme, src in _ELEMENTS:
            if name not in st.element_names():
                st.element_create(name, "from", theme, src)
    except TclError:
        return False

    st.layout("Flat.TEntry", [("Flat.field", {"sticky": "nswe", "border": "1", "children": [
        ("Entry.padding", {"sticky": "nswe", "children": [("Entry.textarea", {"sticky": "nswe"})]})]})])
    st.configure("Flat.TEntry", fieldbackground=p["field"], bordercolor=p["border"],
                 lightcolor=p["field"], foreground=p["fg"], insertcolor=p["fg"], padding=(8, 6))
    st.map("Flat.TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["field"])])

    st.layout("Flat.TCombobox", [
        ("Flat.Combobox.downarrow", {"side": "right", "sticky": "ns"}),
        ("Flat.Combobox.field", {"sticky": "nswe", "children": [
            ("Combobox.padding", {"sticky": "nswe", "children": [
                ("Combobox.textarea", {"sticky": "nswe"})]})]})])
    st.configure("Flat.TCombobox", fieldbackground=p["field"], background=p["field"],
                 bordercolor=p["border"], lightcolor=p["field"], darkcolor=p["field"],
                 troughcolor=p["field"], arrowcolor=p["fg"], arrowsize=14, foreground=p["fg"],
                 padding=(8, 6))
    st.map("Flat.TCombobox",
           fieldbackground=[("readonly", p["field"])], background=[("active", p["heading"])],
           selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["fg"])],
           foreground=[("readonly", p["fg"])], bordercolor=[("focus", p["accent"])])
    root.option_add("*TCombobox*Listbox.background", p["field"])
    root.option_add("*TCombobox*Listbox.foreground", p["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["sel"])
    root.option_add("*TCombobox*Listbox.selectForeground", p["fg"])

    st.layout("Flat.Horizontal.TProgressbar", [("Flat.trough", {"sticky": "nswe", "children": [
        ("Flat.pbar", {"side": "left", "sticky": "ns"})]})])
    st.configure("Flat.Horizontal.TProgressbar", troughcolor=p["trough"], background=p["accent"],
                 bordercolor=p["trough"], lightcolor=p["accent"], darkcolor=p["accent"],
                 thickness=6)

    st.layout("Flat.Vertical.TScrollbar", [("Flat.trough", {"sticky": "ns", "children": [
        ("Flat.thumb", {"expand": "1", "sticky": "nswe"})]})])
    st.configure("Flat.Vertical.TScrollbar", troughcolor=p["bg"], background=p["thumb"],
                 bordercolor=p["bg"], lightcolor=p["thumb"], darkcolor=p["thumb"],
                 arrowsize=10, gripcount=0)
    st.map("Flat.Vertical.TScrollbar", background=[("active", p["thumb_hot"])],
           lightcolor=[("active", p["thumb_hot"])], darkcolor=[("active", p["thumb_hot"])])
    st.layout("Flat.Horizontal.TScrollbar", [("Flat.trough", {"sticky": "we", "children": [
        ("Flat.thumb", {"expand": "1", "sticky": "nswe"})]})])
    st.configure("Flat.Horizontal.TScrollbar", troughcolor=p["bg"], background=p["thumb"],
                 bordercolor=p["bg"], lightcolor=p["thumb"], darkcolor=p["thumb"],
                 arrowsize=10, gripcount=0)
    st.map("Flat.Horizontal.TScrollbar", background=[("active", p["thumb_hot"])],
           lightcolor=[("active", p["thumb_hot"])], darkcolor=[("active", p["thumb_hot"])])

    button_layout = [("Flat.Button.border", {"sticky": "nswe", "border": "1", "children": [
        ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})]
    for name, face, hot, down, text, edge in (
            ("Flat.TButton", p["field"], p["heading_hot"], p["bg"], p["fg"], p["border"]),
            ("Flat.Accent.TButton", p["accent"], p["accent_hot"], p["accent_down"], p["accent_fg"],
             p["accent"])):
        st.layout(name, button_layout)
        st.configure(name, background=face, bordercolor=edge, lightcolor=face, darkcolor=face,
                     foreground=text, padding=(10, 5), anchor="center", relief="flat")
        st.map(name,
               background=[("disabled", p["field_off"]), ("pressed", down), ("active", hot)],
               lightcolor=[("disabled", p["field_off"]), ("pressed", down), ("active", hot)],
               darkcolor=[("disabled", p["field_off"]), ("pressed", down), ("active", hot)],
               bordercolor=[("disabled", p["border"]), ("focus", p["accent"])],
               foreground=[("disabled", p["fg_off"])])

    st.layout("Flat.Treeview", [("Flat.field", {"sticky": "nswe", "border": "1", "children": [
        ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]})])
    st.configure("Flat.Treeview", fieldbackground=p["bg"], background=p["bg"], foreground=p["fg"],
                 bordercolor=p["border"], lightcolor=p["bg"])
    st.map("Flat.Treeview", background=[("selected", p["sel"])],
           foreground=[("selected", p["fg"])])
    st.layout("Flat.Treeview.Heading", [
        ("Flat.Treeheading.cell", {"sticky": "nswe"}),
        ("Treeheading.border", {"sticky": "nswe", "children": [
            ("Treeheading.padding", {"sticky": "nswe", "children": [
                ("Treeheading.image", {"side": "right", "sticky": ""}),
                ("Treeheading.text", {"sticky": "we"})]})]})])
    st.configure("Flat.Treeview.Heading", background=p["heading"], foreground=p["fg"],
                 relief="flat", padding=(6, 5))
    st.map("Flat.Treeview.Heading", background=[("active", p["field"])])
    return True
