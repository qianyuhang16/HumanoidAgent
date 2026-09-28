"""Bounded image context that preserves an action's before/after comparison."""


def paired_images(call_id, before, after):
    images = {}
    for source in dict.fromkeys([*after, *before]):
        if source in before:
            images[f"{call_id}:before:{source}"] = before[source]
        if source in after:
            images[f"{call_id}:after:{source}"] = after[source]
    return images


def select_images(images, comparison, limit):
    if not limit:
        return {}
    # Preserve the first same-view pair, leaving remaining slots for recent
    # observations. Do not spend multiple slots on the exact same image file.
    selected = {}
    pairs = []
    if limit >= 2:
        for source, reference in comparison.items():
            if ":before:" in source:
                after = source.replace(":before:", ":after:", 1)
                if after in comparison:
                    pairs = [(source, reference), (after, comparison[after])]
                    break
    for source, reference in [*pairs, *reversed(list(images.items()))]:
        if reference not in selected.values():
            selected[source] = reference
        if len(selected) >= limit:
            break
    return selected
