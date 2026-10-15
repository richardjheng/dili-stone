"""Structured drawing inputs with deterministic, offline query helpers."""

from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from .common import Id, Model, NonNegativeInt, PositiveInt, Text, unique

Direction = Literal["north", "east", "south", "west"]
Side = Literal["N", "E", "S", "W"]


class Coordinate(Model):
    x: NonNegativeInt
    y: NonNegativeInt


class Place(Model):
    id: Id
    name: Text
    kind: Literal["building", "stop"]
    block: Coordinate
    side: Side
    slot: NonNegativeInt


class MapStep(Model):
    direction: Direction
    blocks: PositiveInt


class Arrival(Model):
    side: Literal["left", "right"]
    position: Literal["corner"] | PositiveInt = Field(
        description="corner，或由終點往回數第 n 棟 building（正整數）；略過空格與 stop。")


class StreetMap(Model):
    kind: Literal["street_map"] = "street_map"
    id: Id
    title: Text
    # Every integer x/y is a street; origin is the southwest intersection.
    width: PositiveInt
    height: PositiveInt
    slots_per_side: Annotated[int, Field(strict=True, ge=2)]
    vertical_streets: tuple[Text, ...]
    horizontal_streets: tuple[Text, ...]
    places: tuple[Place, ...] = Field(min_length=1)
    start: Coordinate
    facing: Direction
    directions: tuple[MapStep, ...] = ()
    arrival: Arrival | None = None

    def _inside(self, point: Coordinate) -> bool:
        return point.x <= self.width and point.y <= self.height

    def endpoint(self, steps: tuple[MapStep, ...] | None = None) -> Coordinate:
        """Return the final intersection, not a place or building entrance."""
        x, y = self.start.x, self.start.y
        vectors = {"north": (0, 1), "east": (1, 0), "south": (0, -1), "west": (-1, 0)}
        for step in self.directions if steps is None else steps:
            dx, dy = vectors[step.direction]
            x, y = x + dx * step.blocks, y + dy * step.blocks
            if not (0 <= x <= self.width and 0 <= y <= self.height):
                raise ValueError("route leaves the street grid")
        return Coordinate(x=x, y=y)

    def _place(self, value: Place | str) -> Place:
        """Queries accept an ID or the matching place from this map."""
        place_id = value.id if isinstance(value, Place) else value
        for place in self.places:
            if place.id == place_id:
                if isinstance(value, Place) and place != value:
                    raise ValueError("place does not match this map")
                return place
        raise ValueError(f"unknown place: {place_id}")

    def _local_position(self, place: Place) -> tuple[int, int]:
        # Integer local coordinates avoid fractions. Corners shared by two
        # sides of ONE block have the same key; opposite blocks stay distinct.
        end = self.slots_per_side - 1
        return {"N": (place.slot, end), "S": (place.slot, 0),
                "E": (end, place.slot), "W": (0, place.slot)}[place.side]

    def on_street(self, a: Place | str) -> str:
        a = self._place(a)
        if a.side in {"N", "S"}:
            return self.horizontal_streets[a.block.y + (a.side == "N")]
        return self.vertical_streets[a.block.x + (a.side == "E")]

    def corner_of(self, a: Place | str) -> tuple[str, str] | None:
        """Return (horizontal street, vertical street), or None mid-block."""
        a = self._place(a)
        end = self.slots_per_side - 1
        if a.slot not in {0, end}:
            return None
        x, y = self._local_position(a)
        return (self.horizontal_streets[a.block.y + (y == end)],
                self.vertical_streets[a.block.x + (x == end)])

    def next_to(self, a: Place | str, b: Place | str) -> bool:
        a, b = self._place(a), self._place(b)
        return (a.kind == b.kind == "building" and a.block == b.block
                and a.side == b.side and abs(a.slot - b.slot) == 1)

    def between(self, a: Place | str, b: Place | str, c: Place | str) -> bool:
        """a is the middle of three consecutive building slots on one side."""
        a, b, c = self._place(a), self._place(b), self._place(c)
        return (self.next_to(a, b) and self.next_to(a, c)
                and min(b.slot, c.slot) < a.slot < max(b.slot, c.slot))

    def across_from(self, a: Place | str, b: Place | str) -> bool:
        a, b = self._place(a), self._place(b)
        opposite = {"N": "S", "S": "N", "E": "W", "W": "E"}
        dx, dy = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}[a.side]
        return (b.side == opposite[a.side] and b.block.x == a.block.x + dx
                and b.block.y == a.block.y + dy and a.slot == b.slot)

    def in_front_of(self, stop: Place | str, building: Place | str) -> bool:
        stop, building = self._place(stop), self._place(building)
        return (stop.kind == "stop" and building.kind == "building"
                and stop.block == building.block and stop.side == building.side
                and stop.slot == building.slot)

    def destination(self) -> Place:
        """Resolve arrival in the last block BEFORE the final intersection."""
        if self.arrival is None:
            raise ValueError("destination requires an arrival")
        if not self.directions:
            raise ValueError("arrival requires at least one MapStep")
        end = self.endpoint()
        direction = self.directions[-1].direction
        left = self.arrival.side == "left"
        # (block x, block y, street-facing side, endpoint corner slot).
        k = self.slots_per_side - 1
        if direction == "north":
            bx, by, side, corner = end.x - int(left), end.y - 1, "E" if left else "W", k
        elif direction == "south":
            bx, by, side, corner = end.x - int(not left), end.y, "W" if left else "E", 0
        elif direction == "east":
            bx, by, side, corner = end.x - 1, end.y - int(not left), "S" if left else "N", k
        else:
            bx, by, side, corner = end.x, end.y - int(left), "N" if left else "S", 0
        candidates = [p for p in self.places if p.kind == "building"
                      and (p.block.x, p.block.y, p.side) == (bx, by, side)]
        if self.arrival.position == "corner":
            matches = [p for p in candidates if p.slot == corner]
        else:
            candidates.sort(key=lambda p: p.slot, reverse=corner == k)
            n = self.arrival.position
            matches = candidates[n - 1:n]
        if len(matches) != 1:
            raise ValueError("arrival must resolve to exactly one building in the final block")
        return matches[0]

    @model_validator(mode="after")
    def grid_valid(self) -> Self:
        if len(self.vertical_streets) != self.width + 1:
            raise ValueError("vertical street names must cover x=0..width")
        if len(self.horizontal_streets) != self.height + 1:
            raise ValueError("horizontal street names must cover y=0..height")
        unique((p.id for p in self.places), "place IDs")
        if not self._inside(self.start):
            raise ValueError("map start outside grid")
        for place in self.places:
            if place.block.x >= self.width or place.block.y >= self.height:
                raise ValueError("place block outside grid")
            if place.slot >= self.slots_per_side:
                raise ValueError("place slot outside side")
        unique(((p.block.x, p.block.y, *self._local_position(p))
                for p in self.places if p.kind == "building"), "building positions (including corners)")
        self.endpoint()
        if self.arrival is not None:
            self.destination()
        return self


class RouteStop(Model):
    id: Id
    name: Text
    # Minutes from the start of service_day, may exceed 1440 for overnight trips.
    arrival_minute: NonNegativeInt
    departure_minute: NonNegativeInt

    @model_validator(mode="after")
    def chronological(self) -> Self:
        if self.departure_minute < self.arrival_minute:
            raise ValueError("departure precedes arrival")
        return self


class Route(Model):
    kind: Literal["route"] = "route"
    id: Id
    title: Text
    service_day: Text
    stops: tuple[RouteStop, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        unique((s.id for s in self.stops), "stop IDs")
        if any(b.arrival_minute < a.departure_minute for a, b in zip(self.stops, self.stops[1:])):
            raise ValueError("route times must follow stop order")
        return self

    def destination(self, start_id: str, stops_ahead: int) -> RouteStop:
        if isinstance(stops_ahead, bool) or not isinstance(stops_ahead, int) or stops_ahead < 0:
            raise ValueError("stops_ahead must be a nonnegative integer")
        ids = [s.id for s in self.stops]
        if start_id not in ids or ids.index(start_id) + stops_ahead >= len(ids):
            raise ValueError("unknown start or destination outside route")
        return self.stops[ids.index(start_id) + stops_ahead]

    def travel_minutes(self, start_id: str, end_id: str) -> int:
        ids = [s.id for s in self.stops]
        if start_id not in ids or end_id not in ids or ids.index(end_id) <= ids.index(start_id):
            raise ValueError("end must follow start on the route")
        return self.stops[ids.index(end_id)].arrival_minute - self.stops[ids.index(start_id)].departure_minute


class TableColumn(Model):
    id: Id
    label: Text
    unit: Text | None = None


class TableRow(Model):
    id: Id
    label: Text
    cells: tuple[Text, ...]


class Table(Model):
    kind: Literal["table"] = "table"
    id: Id
    title: Text
    purpose: Literal["notes", "timetable", "prices", "general"]
    columns: tuple[TableColumn, ...] = Field(min_length=1)
    rows: tuple[TableRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def rectangular(self) -> Self:
        unique((c.id for c in self.columns), "column IDs")
        unique((r.id for r in self.rows), "row IDs")
        if any(len(r.cells) != len(self.columns) for r in self.rows):
            raise ValueError("each row must have one cell per column")
        return self

    def cell(self, row_id: str, column_id: str) -> str:
        rows = {r.id: r for r in self.rows}
        columns = [c.id for c in self.columns]
        if row_id not in rows or column_id not in columns:
            raise ValueError("unknown row or column")
        return rows[row_id].cells[columns.index(column_id)]


class Venn(Model):
    kind: Literal["venn"] = "venn"
    id: Id
    title: Text
    left_label: Text
    right_label: Text
    left: tuple[Text, ...]  # complete sets, including their intersection
    right: tuple[Text, ...]
    intersection: tuple[Text, ...]

    @model_validator(mode="after")
    def consistent_sets(self) -> Self:
        for label in ("left", "right", "intersection"):
            unique(getattr(self, label), f"{label} members")
        if set(self.intersection) != set(self.left) & set(self.right):
            raise ValueError("intersection does not match the two sets")
        return self

    def members(self, region: Literal["left_only", "right_only", "intersection", "union"]) -> tuple[str, ...]:
        regions = {"left_only": set(self.left) - set(self.right),
                   "right_only": set(self.right) - set(self.left),
                   "intersection": set(self.intersection), "union": set(self.left) | set(self.right)}
        if region not in regions:
            raise ValueError("unknown Venn region")
        return tuple(sorted(regions[region]))


Figure = Annotated[StreetMap | Route | Table | Venn, Field(discriminator="kind")]
figure_adapter = TypeAdapter(Figure)
