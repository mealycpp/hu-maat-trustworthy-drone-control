from pathlib import Path
import xml.etree.ElementTree as ET

base = Path.home() / "hu-telepresence-gazebo"
model_file = base / "models/codrone_edu/model.sdf"

tree = ET.parse(model_file)
root = tree.getroot()

model = root.find("model")
assert model is not None, "Model element missing"

links = model.findall("link")
print("Available links:", [x.get("name") for x in links])

# Find the central aircraft body.
body = next(
    (x for x in links if x.get("name", "").split("/")[-1] == "base_link"),
    None
)

assert body is not None, "base_link not found"

# Remove only visuals added by this script on previous runs.
for visual in list(body.findall("visual")):
    if visual.get("name", "").startswith("rscl_"):
        body.remove(visual)

def add_visual(name, pose, geometry, color):
    visual = ET.SubElement(
        body, "visual", {"name": "rscl_" + name}
    )

    ET.SubElement(visual, "pose").text = pose

    geom = ET.SubElement(visual, "geometry")

    if geometry[0] == "box":
        box = ET.SubElement(geom, "box")
        ET.SubElement(box, "size").text = geometry[1]

    elif geometry[0] == "cylinder":
        cylinder = ET.SubElement(geom, "cylinder")
        ET.SubElement(cylinder, "radius").text = geometry[1]
        ET.SubElement(cylinder, "length").text = geometry[2]

    material = ET.SubElement(visual, "material")
    ET.SubElement(material, "ambient").text = color
    ET.SubElement(material, "diffuse").text = color

# Central body shell.
add_visual(
    "body",
    "0 0 0.035 0 0 0",
    ("box", "0.24 0.16 0.055"),
    "0.08 0.09 0.12 1"
)

# Gold upper panel.
add_visual(
    "gold_panel",
    "0 0 0.068 0 0 0",
    ("box", "0.16 0.10 0.008"),
    "0.85 0.57 0.13 1"
)


# Hollow protective guards using short cylinder segments.
# Visual-only geometry; flight dynamics remain unchanged.
import math

for i, (cx, cy) in enumerate([
    (0.22, 0.22),
    (0.22, -0.22),
    (-0.22, 0.22),
    (-0.22, -0.22),
]):
    radius = 0.145
    segments = 24

    for j in range(segments):
        angle = 2 * math.pi * j / segments
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        yaw = angle + math.pi / 2

        add_visual(
            f"guard_{i}_{j}",
            f"{x:.5f} {y:.5f} 0.035 0 0 {yaw:.5f}",
            ("box", "0.040 0.012 0.014"),
            "0.10 0.11 0.15 1"
        )

ET.indent(tree, space="  ")
tree.write(model_file, encoding="unicode",
           xml_declaration=True)

print("CoDrone-inspired visuals added.")
print("Original flight dynamics preserved.")
