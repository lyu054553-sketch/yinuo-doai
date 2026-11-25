#!/usr/bin/env python3
"""Foreground segmentation with the cached U2Net ONNX model."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps


DEFAULT_MODEL = Path.home() / ".u2net" / "u2net.onnx"


def create_session(model_path: Path = DEFAULT_MODEL):
    import onnxruntime as ort

    return ort.InferenceSession(
        str(model_path),
        providers=["CPUExecutionProvider"],
    )


def _mask(image: Image.Image, session) -> Image.Image:
    resized = image.convert("RGB").resize((320, 320), Image.Resampling.LANCZOS)
    array = np.asarray(resized, dtype=np.float32)
    array /= max(float(array.max()), 1e-6)
    array = (array - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    tensor = array.transpose(2, 0, 1)[None, ...]
    output = session.run(None, {session.get_inputs()[0].name: tensor})[0]
    prediction = output[:, 0, :, :]
    minimum, maximum = float(prediction.min()), float(prediction.max())
    prediction = (prediction - minimum) / max(maximum - minimum, 1e-6)
    mask = Image.fromarray(
        (np.clip(prediction[0], 0, 1) * 255).astype(np.uint8),
        mode="L",
    ).resize(image.size, Image.Resampling.LANCZOS)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=1))
    return mask.point(lambda value: 255 if value >= 127 else 0)


def foreground(image: Image.Image):
    """Return an RGBA foreground cutout and its reusable ONNX session."""
    session = create_session()
    image = ImageOps.exif_transpose(image).convert("RGB")
    rgba = image.convert("RGBA")
    rgba.putalpha(_mask(image, session))
    return rgba, session


def foreground_with_session(image: Image.Image, session):
    """Return an RGBA foreground cutout using an existing ONNX session."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    rgba = image.convert("RGBA")
    rgba.putalpha(_mask(image, session))
    return rgba
