from pathlib import Path
import xml.etree.ElementTree as ET

base = Path.home() / "hu-telepresence-gazebo"
src = base / "worlds/hu_fleet.sdf"
dst = base / "worlds/hu_showcase.sdf"

tree = ET.parse(src)
world = tree.getroot().find("world")
assert world is not None

# Preserve original fleet world.
world.set("name", "hu_showcase")

# Remove default ground plane from showcase.
for model in list(world.findall("model")):
    if model.get("name") == "ground_plane":
        world.remove(model)

def add_box(name, pose, size, color, collision=False):
    model = ET.SubElement(world, "model", name=name)
    ET.SubElement(model, "static").text = "true"
    link = ET.SubElement(model, "link", name="body")
    ET.SubElement(link, "pose").text = pose

    visual = ET.SubElement(link, "visual", name="visual")
    geom = ET.SubElement(visual, "geometry")
    box = ET.SubElement(geom, "box")
    ET.SubElement(box, "size").text = size

    material = ET.SubElement(visual, "material")
    ET.SubElement(material, "ambient").text = color
    ET.SubElement(material, "diffuse").text = color
    ET.SubElement(material, "emissive").text = color

    if collision:
        col = ET.SubElement(link, "collision", name="collision")
        cg = ET.SubElement(col, "geometry")
        cb = ET.SubElement(cg, "box")
        ET.SubElement(cb, "size").text = size

# Dark physical floor.
add_box(
    "rscl_floor",
    "0 0 -0.08 0 0 0",
    "30 30 0.15",
    "0.025 0.045 0.075 1",
    collision=True
)

fleet = [
    ("horus", -3, -3, "0.95 0.65 0.16 1"),
    ("ra", 0, -3, "0.12 0.72 0.95 1"),
    ("thoth", 3, -3, "0.60 0.40 0.95 1"),
    ("anubis", -1.5, 1, "0.16 0.85 0.52 1"),
    ("osiris", 1.5, 1, "0.95 0.26 0.42 1"),
]

for name, x, y, color in fleet:
    # Thin visual marker, not an elevated obstacle.
    add_box(
        f"pad_{name}",
        f"{x} {y} 0.004 0 0 0",
        "1.35 1.35 0.008",
        color
    )

    # Dark central pad area.
    add_box(
        f"pad_center_{name}",
        f"{x} {y} 0.009 0 0 0",
        "0.95 0.95 0.008",
        "0.035 0.055 0.085 1"
    )

# Decorative perimeter strips.
for i, y in enumerate((-5.5, 4.5)):
    add_box(
        f"light_strip_{i}",
        f"0 {y} 0.015 0 0 0",
        "12 0.045 0.015",
        "0.12 0.65 0.90 1"
    )

ET.indent(tree, space="  ")
tree.write(dst, encoding="unicode", xml_declaration=True)

print("Created:", dst)
print("Five landing pads and RSCL arena added.")
print("Original hu_fleet.sdf preserved.")
