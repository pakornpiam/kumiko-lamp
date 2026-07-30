#!/usr/bin/env python3
"""
Render preview images of the lamp: the four patterns flat, and the assembled
lantern in three views.

Optional -- needs matplotlib on top of the generator's own requirements, and is
only for eyeballing a design change.  `kumiko_lamp.py` writes SVG previews
without it.

    python3 render_preview.py
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection

from kumiko_lamp import (PATTERNS, Params, build_base, build_cap, build_leg,
                         build_panel, build_post, clip_rect, place_parts)

OUT = Path(__file__).parent / "preview"


def render_patterns(P):
    W, H, b = P.panel_w, P.height, P.panel_border
    ow, oh = W - 2 * b, H - 2 * b

    fig, axes = plt.subplots(1, len(PATTERNS), figsize=(4 * len(PATTERNS), 6))
    for ax, name in zip(axes, sorted(PATTERNS)):
        segs = clip_rect(PATTERNS[name](ow, oh, P.grid),
                         -ow / 2, -oh / 2, ow / 2, oh / 2)
        ax.add_collection(LineCollection(segs, colors="#7a4a20",
                                         linewidths=P.slat_w * 1.6))
        ax.add_patch(plt.Rectangle((-W / 2, -H / 2), W, H, fill=False,
                                   ec="#c8a06a", lw=2))
        ax.set_xlim(-W / 2 - 5, W / 2 + 5)
        ax.set_ylim(-H / 2 - 5, H / 2 + 5)
        ax.set_aspect("equal")
        ax.set_title(f"{name}   ({len(segs)} slats)")
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(OUT / "patterns.png", dpi=110, facecolor="#faf6ee")
    print("wrote", OUT / "patterns.png")


def render_assembly(P, pattern="asanoha"):
    parts = place_parts(P,
                        build_panel(P, pattern),
                        build_post(P, "full"),
                        build_base(P),
                        build_cap(P, pattern),
                        build_leg(P))

    fig = plt.figure(figsize=(13, 6))
    for i, ((elev, azim), title) in enumerate(
            zip([(30, -60), (90, -90), (0, -90)],
                ["isometric", "top", "front"])):
        ax = fig.add_subplot(1, 3, i + 1, projection="3d")
        for name, m in parts.items():
            colour = "#d8bc8a"
            if name in ("base", "cap"):
                colour = "#8a5a2b"
            elif name.startswith("post") or name.startswith("leg"):
                colour = "#a06a33"
            v = m.vertices
            ax.plot_trisurf(v[:, 0], v[:, 1], m.faces, v[:, 2],
                            color=colour, edgecolor="none", shade=True)
        ax.set_box_aspect((P.foot, P.foot, P.total_height))
        ax.view_init(elev=elev, azim=azim)
        ax.set_title(title)
        ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(OUT / "assembly.png", dpi=100, facecolor="#faf6ee")
    print("wrote", OUT / "assembly.png")


if __name__ == "__main__":
    np.seterr(invalid="ignore", divide="ignore")
    OUT.mkdir(parents=True, exist_ok=True)
    P = Params()
    render_patterns(P)
    render_assembly(P)
