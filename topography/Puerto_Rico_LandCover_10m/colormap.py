import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np

# ESA WorldCover 2021 v200 classes
classes = [
    (10,  "Tree cover",               (0,   100,   0)),
    (20,  "Shrubland",                (255, 187,  34)),
    (30,  "Grassland",                (255, 255,  76)),
    (40,  "Cropland",                 (240, 150, 255)),
    (50,  "Built-up",                 (250,   0,   0)),
    (60,  "Bare / sparse vegetation", (180, 180, 180)),
    (70,  "Snow and ice",             (240, 240, 240)),
    (80,  "Permanent water",          (0,   100, 200)),
    (90,  "Herbaceous wetland",       (0,   150, 160)),
    (95,  "Mangroves",                (0,   207, 117)),
    (100, "Moss and lichen",          (250, 230, 160)),
]

# Convert RGB 0-255 to matplotlib 0-1
colors = [tuple(np.array(rgb) / 255.0) for _, _, rgb in classes]
codes = [code for code, _, _ in classes]
labels = [f"{code}  {name}" for code, name, _ in classes]

# Build categorical colormap
cmap = mpl.colors.ListedColormap(colors)

# Boundaries between categories
boundaries = [5, 15, 25, 35, 45, 55, 65, 75, 85, 92.5, 97.5, 105]
norm = mpl.colors.BoundaryNorm(boundaries, cmap.N)

# Create figure
fig, ax = plt.subplots(figsize=(2, 8))
fig.subplots_adjust(left=0.45, right=0.75)

# ScalarMappable for colorbar
sm = mpl.cm.ScalarMappable(cmap=cmap, norm=norm)
sm.set_array([])

cbar = fig.colorbar(
    sm,
    cax=ax,
    boundaries=boundaries,
    ticks=codes,
    spacing="proportional"
)

cbar.ax.set_yticklabels(labels, fontsize=10)
cbar.set_label("ESA WorldCover 2021 v200", fontsize=12)

plt.savefig("worldcover_colorbar.png", dpi=200, bbox_inches="tight")
#plt.show()
