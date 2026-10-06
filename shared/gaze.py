"""The same normalized iris/head features for runtime and training."""

import math


def features(p):
    if len(p) < 478:
        return None

    def eye(corner_a, corner_b, top, bottom, iris):
        a, b = p[corner_a], p[corner_b]
        width, height = abs(b.x - a.x), abs(p[bottom].y - p[top].y)
        if width < 0.015 or height < 0.003 or height / width < 0.12:
            return None
        return [
            (p[iris].x - min(a.x, b.x)) / width,
            (p[iris].y - min(p[top].y, p[bottom].y)) / height,
        ]

    left, right = eye(33, 133, 159, 145, 468), eye(362, 263, 386, 374, 473)
    width, height = abs(p[454].x - p[234].x), abs(p[152].y - p[10].y)
    if left is None or right is None or width < 0.1 or height < 0.1:
        return None
    vector = [
        (left[0] + right[0]) / 2,
        (left[1] + right[1]) / 2,
        (p[1].x - p[234].x) / width,
        (p[1].y - p[10].y) / height,
    ]
    return vector if all(math.isfinite(v) for v in vector) else None


def predict_offscreen(model, vector):
    """Read-only inference on the exported JSON forest; no pickle execution."""
    if (
        model.get("schema") != 1
        or len(vector) != 4
        or not all(math.isfinite(x) for x in vector)
    ):
        raise ValueError("INVALID_GAZE_INPUT")
    probabilities = []
    for tree in model["trees"]:
        node = 0
        for _ in range(len(tree["left"])):
            if tree["left"][node] == -1:
                probabilities.append(tree["off_probability"][node])
                break
            node = (
                tree["left"][node]
                if vector[tree["feature"][node]] <= tree["threshold"][node]
                else tree["right"][node]
            )
        else:
            raise ValueError("INVALID_GAZE_TREE")
    if not probabilities:
        raise ValueError("EMPTY_GAZE_MODEL")
    return sum(probabilities) / len(probabilities)
