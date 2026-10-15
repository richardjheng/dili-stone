"""Draw Figure models as PNG images; tables are laid out natively by the docx writer."""

from io import BytesIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, Rectangle  # noqa: E402

from ..models import Route, StreetMap, Table, Venn

plt.rcParams["font.family"] = ["Times New Roman", "Microsoft JhengHei", "DejaVu Sans"]


def _png(fig) -> bytes:
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


def _clock(minute: int) -> str:
    return f"{minute // 60 % 24}:{minute % 60:02d}"


def render_venn(venn: Venn) -> bytes:
    fig, ax = plt.subplots(figsize=(6, 3.6))
    for x, label in ((-0.6, venn.left_label), (0.6, venn.right_label)):
        ax.add_patch(Circle((x, 0), 1.15, fill=False, linewidth=1.5))
        ax.text(x, 1.3, label, ha="center", va="bottom", fontsize=12, fontweight="bold")
    columns = ((-1.15, venn.members("left_only")), (0, venn.members("intersection")),
               (1.15, venn.members("right_only")))
    for x, members in columns:
        ax.text(x, 0, "\n".join(members), ha="center", va="center", fontsize=9, wrap=True)
    ax.text(0, -1.45, venn.title, ha="center", va="top", fontsize=11)
    ax.set_xlim(-2, 2)
    ax.set_ylim(-1.7, 1.7)
    ax.set_aspect("equal")
    ax.axis("off")
    return _png(fig)


def render_route(route: Route) -> bytes:
    count = len(route.stops)
    fig, ax = plt.subplots(figsize=(max(4, 1.5 * count), 1.3))
    ax.plot([0, count - 1], [0, 0], color="black", linewidth=2)
    for i, stop in enumerate(route.stops):
        ax.plot(i, 0, "o", markersize=10, markerfacecolor="white", markeredgecolor="black")
        ax.text(i, 0.25, stop.name, ha="center", va="bottom", fontsize=10)
        times = _clock(stop.arrival_minute)
        if stop.departure_minute != stop.arrival_minute:
            times += f"–{_clock(stop.departure_minute)}"
        ax.text(i, -0.25, times, ha="center", va="top", fontsize=9)
    ax.set_title(f"{route.title} ({route.service_day})", fontsize=11)
    ax.set_xlim(-0.6, count - 0.4)
    ax.set_ylim(-0.6, 0.6)
    ax.axis("off")
    return _png(fig)


STREET = 0.09  # half width of a street, in block units


def _cell(street_map: StreetMap, place) -> tuple[float, float, float]:
    """Lower-left corner and size of the square a place occupies inside its block."""
    size = (1 - 2 * STREET) / street_map.slots_per_side
    left, bottom = place.block.x + STREET, place.block.y + STREET
    far = 1 - 2 * STREET - size
    offset = place.slot * size
    x, y = {"N": (offset, far), "S": (offset, 0), "E": (far, offset), "W": (0, offset)}[place.side]
    return left + x, bottom + y, size


def _stop_point(street_map: StreetMap, stop) -> tuple[float, float]:
    """A stop stands on the sidewalk in front of its slot."""
    x, y, size = _cell(street_map, stop)
    edge = STREET * 0.55
    return {"N": (x + size / 2, y + size + edge), "S": (x + size / 2, y - edge),
            "E": (x + size + edge, y + size / 2), "W": (x - edge, y + size / 2)}[stop.side]


def render_street_map(street_map: StreetMap) -> bytes:
    """Streets, blocks, labelled buildings and stops, the start star and a compass.

    The route and the destination are question content, so they are never drawn.
    """
    width, height = street_map.width, street_map.height
    fig, ax = plt.subplots(figsize=(2.6 * width + 1.2, 2.6 * height + 0.9))
    ax.add_patch(Rectangle((-STREET, -STREET), width + 2 * STREET, height + 2 * STREET,
                           color="#d9d9d9", zorder=0))
    for bx in range(width):
        for by in range(height):
            ax.add_patch(Rectangle((bx + STREET, by + STREET), 1 - 2 * STREET, 1 - 2 * STREET,
                                   facecolor="white", edgecolor="#bbbbbb", zorder=1))
    for x, name in enumerate(street_map.vertical_streets):
        ax.text(x, -STREET - 0.06, name, ha="center", va="top", fontsize=9, rotation=90)
    for y, name in enumerate(street_map.horizontal_streets):
        ax.text(width + STREET + 0.06, y, name, ha="left", va="center", fontsize=9)
    for place in street_map.places:
        if place.kind == "building":
            x, y, size = _cell(street_map, place)
            ax.add_patch(Rectangle((x, y), size, size, facecolor="#f4f4f4", edgecolor="black",
                                   linewidth=1, zorder=2))
            longest = max(len(word) for word in place.name.split())
            ax.text(x + size / 2, y + size / 2, place.name.replace(" ", "\n"), ha="center",
                    va="center", fontsize=min(7, 56 / longest), zorder=3)
    for place in street_map.places:
        if place.kind == "stop":
            x, y = _stop_point(street_map, place)
            ax.plot(x, y, "s", markersize=6, color="black", zorder=4)
            ax.text(x, y + 0.05, place.name, ha="center", va="bottom", fontsize=6.5, zorder=4,
                    bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.5})
    start = street_map.start
    ax.plot(start.x, start.y, "*", markersize=16, color="black", zorder=5)
    ax.annotate("You are here", (start.x, start.y), xytext=(start.x + 0.12, start.y + 0.12),
                fontsize=8, zorder=5, bbox={"facecolor": "white", "edgecolor": "black", "pad": 1.5})
    ax.annotate("", xy=(-0.35, height - 0.15), xytext=(-0.35, height - 0.5),
                arrowprops={"arrowstyle": "->", "linewidth": 1.5})
    ax.text(-0.35, height - 0.12, "N", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_title(street_map.title, fontsize=11)
    ax.set_xlim(-0.55, width + 0.9)
    ax.set_ylim(-0.75, height + 0.25)
    ax.set_aspect("equal")
    ax.axis("off")
    return _png(fig)


def render(figure) -> bytes | None:
    """PNG bytes for drawn figures; None for tables, which the docx writer builds as tables."""
    if isinstance(figure, Table):
        return None
    if isinstance(figure, Venn):
        return render_venn(figure)
    if isinstance(figure, Route):
        return render_route(figure)
    if isinstance(figure, StreetMap):
        return render_street_map(figure)
    raise TypeError(f"unknown figure kind: {type(figure).__name__}")
