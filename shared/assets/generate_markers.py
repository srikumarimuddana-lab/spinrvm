"""Regenerate the bundled car marker PNGs from the high-res art in marker-src/.

Why these exist: the original markers were single 256x527 PNGs rendered into a
32-44pt map marker. That ~13:1 downscale happens inside the native marker
snapshot with no mipmapping, so the car's dark outline aliased into ragged,
"eaten" edges on device. The fix is to pre-render square, density-suffixed
assets (@1x/@2x/@3x for the 44pt max render size, see CarMarker call sites)
with high-quality Lanczos downsampling, plus a subtle white outline and a soft
ambient shadow so the marker reads crisply on any map background.

Output goes to both copies of the assets (shared/assets for the rider app via
@spinr/shared, driver-app/assets/images for the driver app's own CarMarker).

Usage:  python3 shared/assets/generate_markers.py   (from the repo root;
        requires pillow + numpy)
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = Path(__file__).resolve().parent / "marker-src"
OUT_DIRS = [
    REPO_ROOT / "shared" / "assets",
    REPO_ROOT / "driver-app" / "assets" / "images",
]
VARIANTS = ["car_marker", "car_marker_xl", "car_marker_premium"]

# Final square canvas per density. Base = 44pt, the largest CarMarker `size`
# used by any call site; smaller sizes downscale slightly, which is fine.
SIZES = {"": 44, "@2x": 88, "@3x": 132}

# Radii in source pixels (the art is ~527px tall).
HALO_R = 10          # white outline thickness
HALO_SMOOTH = 2.0    # soften the outline edge
SHADOW_BLUR = 16     # ambient shadow spread
SHADOW_ALPHA = 0.30  # shadow opacity
EDGE_PAD = HALO_R + SHADOW_BLUR * 2 + 8  # room so filters never clip


def build(variant: str) -> Image.Image:
    src = Image.open(SRC_DIR / f"{variant}.png").convert("RGBA")
    src = src.crop(src.getbbox())  # trim transparent margins

    w, h = src.size
    canvas = Image.new("RGBA", (w + 2 * EDGE_PAD, h + 2 * EDGE_PAD), (0, 0, 0, 0))
    canvas.alpha_composite(src, (EDGE_PAD, EDGE_PAD))

    alpha = canvas.getchannel("A")

    # White outline: dilate the car silhouette, then smooth the edge.
    halo_mask = alpha.filter(ImageFilter.MaxFilter(2 * HALO_R + 1))
    halo_mask = halo_mask.filter(ImageFilter.GaussianBlur(HALO_SMOOTH))
    halo = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    halo.putalpha(halo_mask)

    # Centered ambient shadow — rotation-invariant under the marker's `flat`
    # rotation, unlike a directional drop shadow.
    shadow_mask = halo_mask.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    shadow_a = (np.asarray(shadow_mask, dtype=np.float32) * SHADOW_ALPHA).astype(np.uint8)
    shadow = Image.new("RGBA", canvas.size, (15, 23, 42, 0))
    shadow.putalpha(Image.fromarray(shadow_a))

    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.alpha_composite(shadow)
    out.alpha_composite(halo)
    out.alpha_composite(canvas)

    # Tight-trim the near-invisible shadow fringe, then pad to square so the
    # car centers exactly in the component's size x size box.
    a = np.asarray(out.getchannel("A"))
    rows = np.where(a.max(axis=1) > 6)[0]
    cols = np.where(a.max(axis=0) > 6)[0]
    out = out.crop((cols[0], rows[0], cols[-1] + 1, rows[-1] + 1))

    side = max(out.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(out, ((side - out.width) // 2, (side - out.height) // 2))
    return square


def main() -> None:
    for variant in VARIANTS:
        square = build(variant)
        for suffix, px in SIZES.items():
            img = square.resize((px, px), Image.LANCZOS)
            for out_dir in OUT_DIRS:
                img.save(out_dir / f"{variant}{suffix}.png", optimize=True)
        print(f"{variant}: {square.size[0]}px master -> {list(SIZES.values())} px")


if __name__ == "__main__":
    main()
