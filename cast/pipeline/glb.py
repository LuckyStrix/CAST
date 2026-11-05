"""Write a vertex-coloured triangle mesh as binary glTF (GLB). numpy + struct only.

glTF is right-handed, Y-up, metres; COLOR_0 is linear light, so sRGB colours are decoded.
(From FleshForge's glbwrite, same author, MIT.)
"""

import json
import struct

import numpy as np

_FLOAT, _UINT32 = 5126, 5125
_ARRAY_BUFFER, _ELEMENT_ARRAY_BUFFER = 34962, 34963


def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def write_glb(path, vertices, faces, colors_srgb=None, normals=None, name="scan"):
    vertices = np.ascontiguousarray(vertices, dtype="<f4")
    faces = np.ascontiguousarray(faces, dtype="<u4")
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices):
        raise ValueError("vertices must be a non-empty (V, 3) array")
    if faces.ndim != 2 or faces.shape[1] != 3 or not len(faces) or faces.max() >= len(vertices):
        raise ValueError("faces must be a non-empty (F, 3) array of valid vertex indices")

    chunks, views, accessors, attributes = [], [], [], {}

    def add(data, target, comp, kind, minmax=False):
        offset = sum(len(c) for c in chunks)
        raw = data.tobytes()
        chunks.append(raw + b"\x00" * (-len(raw) % 4))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(raw), "target": target})
        acc = {"bufferView": len(views) - 1, "componentType": comp, "count": len(data),
               "type": kind}
        if minmax:
            acc["min"], acc["max"] = data.min(axis=0).tolist(), data.max(axis=0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    attributes["POSITION"] = add(vertices, _ARRAY_BUFFER, _FLOAT, "VEC3", minmax=True)
    if normals is not None:
        attributes["NORMAL"] = add(np.ascontiguousarray(normals, dtype="<f4"), _ARRAY_BUFFER,
                                   _FLOAT, "VEC3")
    if colors_srgb is not None:
        lin = np.clip(srgb_to_linear(np.clip(colors_srgb, 0, 1)), 0, 1)
        attributes["COLOR_0"] = add(np.ascontiguousarray(lin, dtype="<f4"), _ARRAY_BUFFER,
                                    _FLOAT, "VEC3")
    indices = add(faces.reshape(-1), _ELEMENT_ARRAY_BUFFER, _UINT32, "SCALAR")

    binary = b"".join(chunks)
    doc = {
        "asset": {"version": "2.0", "generator": "CAST"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": [
            {"attributes": attributes, "indices": indices, "material": 0, "mode": 4}]}],
        "materials": [{"name": "scan", "doubleSided": True, "pbrMetallicRoughness": {
            "baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0, "roughnessFactor": 1.0}}],
        "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors,
    }
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(binary)))
        fh.write(struct.pack("<II", len(text), 0x4E4F534A) + text)
        fh.write(struct.pack("<II", len(binary), 0x004E4942) + binary)
