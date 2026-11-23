"""Reading a JSON-stat cube without trusting the order of its dimensions."""
from __future__ import annotations


def cells(cube: dict) -> dict[tuple[str, ...], float]:
    """{(category code of every dimension wider than one, in cube order): value}.

    JSON-stat stores values in row-major order with the last dimension fastest. Positions are
    computed from the cube's own `id` and `size`, so a reordered dimension cannot shift a value onto
    the wrong partner or month.
    """
    ids = list(cube.get("id") or [])
    sizes = list(cube.get("size") or [])
    dims = cube.get("dimension") or {}
    if not ids or len(ids) != len(sizes):
        return {}
    codes: list[list[str]] = []
    for name, size in zip(ids, sizes):
        index = ((dims.get(name) or {}).get("category") or {}).get("index") or {}
        if isinstance(index, list):
            ordered = list(index)
        else:
            ordered = [code for code, _ in sorted(index.items(), key=lambda kv: kv[1])]
        if len(ordered) != size:
            # A single-category dimension may be described without an index.
            ordered = ordered or [""] * size
        codes.append(ordered)
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    wide = [i for i, size in enumerate(sizes) if size > 1]
    values = cube.get("value") or {}
    if isinstance(values, list):
        values = {str(i): v for i, v in enumerate(values) if v is not None}
    out: dict[tuple[str, ...], float] = {}
    for position, value in values.items():
        if value is None:
            continue
        flat = int(position)
        key = []
        for i in wide:
            key.append(codes[i][(flat // strides[i]) % sizes[i]])
        out[tuple(key)] = float(value)
    return out


def dimension_labels(cube: dict, name: str) -> dict[str, str]:
    return dict((((cube.get("dimension") or {}).get(name) or {}).get("category") or {}).get("label") or {})
