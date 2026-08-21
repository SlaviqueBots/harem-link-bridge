"""Entry point: python -m conjure_finder"""

from __future__ import annotations

import tkinter as tk

from conjure_finder.bootstrap import ensure_path, load_env


def main() -> None:
    ensure_path()
    load_env()
    from conjure_finder import __version__
    from conjure_finder.settings import apply_settings_file, settings_status

    apply_settings_file()
    from conjure_finder.gui import ConjureFinderApp

    root = tk.Tk()
    root.title(f"Conjure Finder  v{__version__}")
    root.minsize(720, 560)
    root.geometry("820x640")
    app = ConjureFinderApp(root, embedded=False)
    app.pack(fill=tk.BOTH, expand=True)
    st = settings_status()
    if not (st["danbooru"] or st["rule34"]):
        root.after(
            300,
            lambda: app.status_var.set(
                "No API keys found — open Settings… to add Danbooru / Rule34 credentials."
            ),
        )
    root.mainloop()


if __name__ == "__main__":
    main()
