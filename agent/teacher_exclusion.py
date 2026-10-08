"""Conservative one-to-one association before excluding a known teacher."""
import math


def iou(a, b):
    left, top, right, bottom = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    area = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area += max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    return intersection / (area - intersection) if area > intersection else 0


def count_students(raw_count, primary, identities, width, height, engine, templates):
    boxes = [[x / width if index % 2 == 0 else x / height for index, x in enumerate(item['box'])]
             for item in primary]
    used = set()
    for face in identities:
        box = face.get('box')
        if (not isinstance(box, list) or len(box) != 4
                or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in box)):
            continue
        x, y, w, h = box
        matches = [index for index, item in enumerate(boxes) if iou(item, [x, y, x + w, y + h]) >= .35]
        # An ambiguous/duplicate correspondence cannot exclude a second primary face.
        if len(matches) == 1 and matches[0] not in used and engine.match(face.get('embedding'), templates) is not None:
            used.add(matches[0])
    return max(0, max(raw_count, len(identities)) - len(used))
