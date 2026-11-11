#!/usr/bin/env python3
"""Upgrade existing HU Gazebo world visuals; preserves dynamics and controller plugins."""
from pathlib import Path
import xml.etree.ElementTree as ET
import math, shutil

BASE=Path.home()/ 'hu-telepresence-gazebo'
MODEL=BASE/'models/codrone_edu/model.sdf'
WORLD=BASE/'worlds/hu_showcase.sdf'
assert MODEL.is_file() and WORLD.is_file(), 'Run on the WSL machine after creating the existing HU workspace'
for path in (MODEL,WORLD):
    backup=path.with_suffix('.before_showcase.sdf')
    if not backup.exists(): shutil.copy2(path,backup)

tr=ET.parse(MODEL); root=tr.getroot(); model=root.find('model'); assert model is not None
body=next((x for x in model.findall('link') if x.get('name','').endswith('/base_link')),None)
assert body is not None
for v in list(body.findall('visual')): body.remove(v)

def visual(parent,name,pose,shape,rgba):
    v=ET.SubElement(parent,'visual',name=name)
    ET.SubElement(v,'pose').text=' '.join(map(str,pose))
    g=ET.SubElement(v,'geometry'); s=ET.SubElement(g,shape[0]);
    for k,val in shape[1].items(): ET.SubElement(s,k).text=str(val)
    mat=ET.SubElement(v,'material')
    for tag in ('ambient','diffuse'): ET.SubElement(mat,tag).text=rgba
    return v

# No changes to link mass, inertias, collisions, rotor joints, or plugin tuning.
visual(body,'rscl_shell',(0,0,0.035,0,0,0),('box',{'size':'0.27 0.19 0.07'}),'0.035 0.05 0.08 1')
visual(body,'rscl_top',(0,0,0.076,0,0,0),('box',{'size':'0.19 0.13 0.012'}),'0.9 0.62 0.16 1')
visual(body,'rscl_nose',(0.145,0,0.045,0,0,0),('sphere',{'radius':'0.025'}),'0.1 0.85 0.95 1')
# Use rotor link locations where available, instead of assuming an arbitrary rotor spacing.
rotors=[]
for link in model.findall('link'):
    if '/rotor_' in link.get('name',''):
        p=(link.findtext('pose') or '').split()
        if len(p)>=3:
            try: rotors.append(tuple(float(v) for v in p[:3]))
            except ValueError: pass
if len(rotors)!=4:
    rotors=[(0.22,0.22,0.035),(0.22,-0.22,0.035),(-0.22,0.22,0.035),(-0.22,-0.22,0.035)]
for i,(cx,cy,cz) in enumerate(rotors):
    radius=0.14
    for j in range(32):
        a=2*math.pi*j/32
        x=cx+radius*math.cos(a); y=cy+radius*math.sin(a)
        visual(body,f'rscl_guard_{i}_{j}',(round(x,5),round(y,5),round(cz,5),0,0,round(a+math.pi/2,5)),('box',{'size':'0.031 0.013 0.018'}),'0.08 0.12 0.18 1')
    # Two thin radial support struts per guard.
    for j,a in enumerate((0,math.pi/2)):
        visual(body,f'rscl_support_{i}_{j}',(cx,cy,cz,0,0,a),('box',{'size':'0.26 0.008 0.008'}),'0.15 0.18 0.22 1')
ET.indent(tr,space='  ');tr.write(MODEL,encoding='unicode',xml_declaration=True)

# Make a copy of the existing showcase, add simple high-contrast markers without changing dynamics.
wt=ET.parse(WORLD); w=wt.getroot().find('world');assert w is not None
for m in list(w.findall('model')):
    if m.get('name','').startswith('rscl_label_'):w.remove(m)
colors={'horus':'0.98 0.73 0.22 1','ra':'0.12 0.85 0.95 1','thoth':'0.73 0.49 0.99 1','anubis':'0.3 0.92 0.55 1','osiris':'1 0.32 0.48 1'}
positions={'horus':(-3,-3),'ra':(0,-3),'thoth':(3,-3),'anubis':(-1.5,1),'osiris':(1.5,1)}
for name,(x,y) in positions.items():
    m=ET.SubElement(w,'model',name='rscl_label_'+name);ET.SubElement(m,'static').text='true'
    link=ET.SubElement(m,'link',name='marker')
    visual(link,'beacon',(x,y,0.03,0,0,0),('cylinder',{'radius':'0.13','length':'0.025'}),colors[name])
ET.indent(wt,space='  ');wt.write(WORLD,encoding='unicode',xml_declaration=True)
print('Updated:',MODEL,'and',WORLD)
print('Backups saved. Flight dynamics and controllers unchanged.')
print('Run: gz sdf -k '+str(WORLD))
print('Then: gz sim -r -v 3 '+str(WORLD))
