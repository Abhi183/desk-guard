"""A small always-on-top pop-up in the top-right corner of the screen.

Runs as its own short-lived process so the camera loop never waits on it:
    python -m webcam_fun.toast TITLE MESSAGE [COLOR] [SECONDS]
Click it to dismiss early.
"""

import sys
import tkinter as tk

from webcam_fun.dock import hide_from_dock

WIDTH, HEIGHT = 380, 92
MARGIN_X, MARGIN_Y = 16, 40  # below the menu bar
BG, FG, MUTED = "#1e1e1e", "#ffffff", "#c8c8c8"
DEFAULT_COLOR = "#e5484d"
DEFAULT_SECONDS = 5.0
FADE_STEPS = 10


def show(title: str, message: str, color: str, seconds: float) -> None:
    root = tk.Tk()
    hide_from_dock()  # each pop-up is its own process; keep it out of the Dock
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=BG)
    x = root.winfo_screenwidth() - WIDTH - MARGIN_X
    root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{MARGIN_Y}")

    tk.Frame(root, bg=color, width=8).pack(side="left", fill="y")
    body = tk.Frame(root, bg=BG, padx=14, pady=10)
    body.pack(side="left", fill="both", expand=True)
    tk.Label(body, text=title, bg=BG, fg=FG, font=("Helvetica", 16, "bold"),
             anchor="w").pack(fill="x")
    tk.Label(body, text=message, bg=BG, fg=MUTED, font=("Helvetica", 13), anchor="w",
             justify="left", wraplength=WIDTH - 50).pack(fill="x", pady=(4, 0))

    for widget in (root, body, *body.winfo_children()):
        widget.bind("<Button-1>", lambda _e: root.destroy())

    def fade(step: int = FADE_STEPS) -> None:
        if step <= 0:
            root.destroy()
            return
        root.attributes("-alpha", step / FADE_STEPS)
        root.after(40, fade, step - 1)

    root.after(int(seconds * 1000), fade)
    root.lift()
    root.mainloop()


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        sys.exit("usage: python -m webcam_fun.toast TITLE MESSAGE [COLOR] [SECONDS]")
    color = argv[2] if len(argv) > 2 else DEFAULT_COLOR
    try:
        seconds = float(argv[3]) if len(argv) > 3 else DEFAULT_SECONDS
    except ValueError:
        seconds = DEFAULT_SECONDS
    show(argv[0][:60], argv[1][:160], color, min(max(seconds, 1.0), 30.0))


if __name__ == "__main__":
    main(sys.argv[1:])
