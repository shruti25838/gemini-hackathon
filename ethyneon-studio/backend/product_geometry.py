from functools import lru_cache
from typing import Any, Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from backend.gemini_schema import gemini_response_schema


class Vector(BaseModel):
    x: float
    y: float
    z: float

    def array(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z])


class Part(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    shape: Literal["box", "rounded_box", "cylinder", "ellipsoid"]
    center: Vector
    size: Vector
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    material: Literal["fabric", "mesh", "leather", "wood", "metal", "plastic", "other"] = "other"
    recolorable: bool = False
    yaw: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)
    pitch: float = Field(default=0, ge=-360, le=360, allow_inf_nan=False)


class MeshRecipe(BaseModel):
    width_over_height: float = Field(gt=0, le=40, allow_inf_nan=False)
    depth_over_height: float = Field(gt=0, le=40, allow_inf_nan=False)
    parts: list[Part] = Field(min_length=1, max_length=32)


def gemini_recipe_schema() -> dict[str, Any]:
    return gemini_response_schema(MeshRecipe)


ROUGHNESS = {
    "fabric": 0.94, "mesh": 0.92, "leather": 0.66, "wood": 0.66, "metal": 0.42, "plastic": 0.76, "other": 0.84,
}


@lru_cache(maxsize=4)
def surface_pattern(material: str) -> Image.Image | None:
    if material not in {"fabric", "mesh", "leather", "wood"}:
        return None
    y, x = np.indices((64, 64))
    noise = np.random.default_rng(42).normal(0, 3, (64, 64))
    warp = np.cos(x * np.pi / 4)
    weft = np.cos(y * np.pi / 4)
    if material == "mesh":
        shade = 195 + 48 * np.maximum(warp, weft)
    elif material == "fabric":
        shade = 232 + 9 * warp + 9 * weft
    elif material == "leather":
        shade = 238 + 5 * np.cos(x * np.pi / 4 + weft) * weft
    else:
        shade = 231 + 15 * np.cos(y * np.pi / 8 + np.sin(x * np.pi / 32))
    pixels = np.clip(shade + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(np.repeat(pixels[:, :, None], 3, axis=2))


def shape_mesh(item: Part) -> trimesh.Trimesh:
    if item.shape == "cylinder":
        mesh = trimesh.creation.cylinder(radius=0.5, height=1.0, sections=24)
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    elif item.shape in {"ellipsoid", "rounded_box"}:
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=0.5)
        if item.shape == "rounded_box":
            mesh.vertices = 0.5 * np.sign(mesh.vertices) * np.abs(2 * mesh.vertices) ** 0.38
    else:
        mesh = trimesh.creation.box()

    if item.shape in {"ellipsoid", "rounded_box"}:
        normals = trimesh.geometry.weighted_vertex_normals(
            len(mesh.vertices), mesh.faces, mesh.face_normals, mesh.face_angles, use_loop=True,
        )[mesh.faces.flatten()]
    else:
        normals = np.repeat(mesh.face_normals, 3, axis=0)
    normal_axis = np.abs(mesh.face_normals).argmax(axis=1)
    mesh.unmerge_vertices()
    mesh.vertex_normals = normals

    pattern = surface_pattern(item.material)
    uv = None
    if pattern is not None:
        uv = np.zeros((len(mesh.vertices), 2))
        for axis, plane in enumerate(((2, 1), (0, 2), (0, 1))):
            mask = np.repeat(normal_axis == axis, 3)
            uv[mask] = (mesh.vertices[mask][:, plane] + 0.5) * 3
    srgb = np.array(list(bytes.fromhex(item.color[1:]))) / 255.0
    linear = trimesh.visual.color.srgb_to_linear(srgb)
    mesh.visual = TextureVisuals(uv=uv, material=PBRMaterial(
        name=f"{'finish' if item.recolorable else 'fixed'}_{item.material}",
        baseColorFactor=np.rint(np.r_[linear, 1.0] * 255).astype(np.uint8),
        baseColorTexture=pattern,
        metallicFactor=0.55 if item.material == "metal" else 0.0,
        roughnessFactor=ROUGHNESS[item.material],
    ))
    return mesh


def build_glb(recipe: MeshRecipe, dimensions: dict[str, float] | None) -> bytes:
    """Build a floor-aligned, Y-up proxy with the front facing +Z."""
    parts = []
    for item in recipe.parts:
        center, size = item.center.array(), item.size.array()
        if not np.isfinite([center, size]).all() or np.any(size < 0.0001):
            raise ValueError(f"Invalid mesh dimensions for {item.label}.")
        if np.any(center - size / 2 < -0.025) or np.any(center + size / 2 > 1.025):
            raise ValueError(f"Mesh component {item.label} exceeded the normalized product bounds.")
        mesh = shape_mesh(item)
        mesh.apply_scale(size)
        if item.pitch:
            mesh.apply_transform(trimesh.transformations.rotation_matrix(np.radians(item.pitch), [1, 0, 0]))
        if item.yaw:
            mesh.apply_transform(trimesh.transformations.rotation_matrix(np.radians(item.yaw), [0, 1, 0]))
        mesh.apply_translation(center)
        parts.append((item.label, mesh))

    lower = np.min([mesh.bounds[0] for _, mesh in parts], axis=0)
    upper = np.max([mesh.bounds[1] for _, mesh in parts], axis=0)
    output_size = (
        [dimensions[key] for key in ("width", "height", "depth")]
        if dimensions else [recipe.width_over_height, 1.0, recipe.depth_over_height]
    )
    origin = (lower + upper) / 2
    origin[1] = lower[1]
    scale = np.array(output_size) / (upper - lower)
    scene = trimesh.Scene()
    for index, (label, mesh) in enumerate(parts):
        mesh.apply_translation(-origin)
        mesh.apply_scale(scale)
        scene.add_geometry(mesh, node_name=f"{index + 1} {label}")
    return trimesh.exchange.gltf.export_glb(scene, include_normals=True)
