from pathlib import Path
import copy
import xml.etree.ElementTree as ET

root = Path.home() / "hu-telepresence-gazebo"
source = root / "worlds/reference_multicopter.sdf"
destination = root / "worlds/hu_fleet.sdf"

tree = ET.parse(source)
sdf = tree.getroot()
world = sdf.find("world")
world.set("name", "hu_fleet")

# Preserve the working Gazebo world systems and environment.
# Remove the original X3/X4 aircraft.
includes = world.findall("include")
assert len(includes) == 2, (
    f"Expected 2 aircraft includes, found {len(includes)}"
)

x3_template = copy.deepcopy(includes[0])

for inc in includes:
    world.remove(inc)

fleet = [
    ("Horus",   -3, -3, 0.5),
    ("Ra",       0, -3, 0.5),
    ("Thoth",    3, -3, 0.5),
    ("Anubis",  -1.5, 1, 0.5),
    ("Osiris",   1.5, 1, 0.5),
]

for name, x, y, z in fleet:
    aircraft = copy.deepcopy(x3_template)

    # Each instance must have a unique Gazebo model name.
    ET.SubElement(aircraft, "name").text = name.lower()
    ET.SubElement(aircraft, "pose").text = (
        f"{x} {y} {z} 0 0 0"
    )

    # Match the original X3 model's scoped links and joints.
    # Give each controller its own command namespace.
    for plugin in aircraft.findall("plugin"):
        namespace = plugin.find("robotNamespace")
        if namespace is not None:
            namespace.text = name

        for tag in ("jointName", "linkName", "comLinkName"):
            node = plugin.find(tag)
            if node is not None and node.text:
                node.text = node.text.replace("X3/", "")

        for joint in plugin.findall(
            ".//rotorConfiguration/rotor/jointName"
        ):
            if joint.text:
                joint.text = joint.text.replace("X3/", "")

    world.append(aircraft)

ET.indent(tree, space="  ")
tree.write(destination, encoding="unicode",
           xml_declaration=True)

print(f"Created: {destination}")
print("Fleet:", ", ".join(x[0] for x in fleet))
