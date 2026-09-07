#!/usr/bin/env python3
"""Headless review render from actual CAD STL triangles (no OpenGL required)."""
from pathlib import Path
import struct
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'mechanical/rev-a/build'
COLORS={'flash_envelope':'#5b4272','base':'#344457','lid':'#8292a5','pcb_proxy':'#258873','ssd_2230_proxy':'#3b6690','thermal_insert':'#d8a257','thermal_pad':'#7ca5ab','asm_package_envelope':'#222c35','service_connector_envelope':'#e4bd47','isolation_shunts_envelope':'#d55b42'}
def triangles(path):
    data=path.read_bytes();n=struct.unpack_from('<I',data,80)[0]
    dtype=np.dtype([('normal','<f4',3),('vertices','<f4',(3,3)),('attribute','<u2')])
    return np.frombuffer(data,offset=84,count=n,dtype=dtype)['vertices'].copy()
def render(exploded):
    fig=plt.figure(figsize=(12,9),facecolor='#f5f7fa');ax=fig.add_subplot(111,projection='3d');ax.set_facecolor('#f5f7fa')
    meshes=[]; colors=[]
    for name,color in COLORS.items():
        v=triangles(OUT/f'{name}.stl')
        if name=='lid':v[:,:,2]+=6.9+(24 if exploded else 0)
        elif name=='thermal_insert' and exploded:v[:,:,2]+=24
        elif name=='thermal_pad' and exploded:v[:,:,2]+=16
        elif exploded and name not in ('base','lid'):v[:,:,2]+=10
        meshes.append(v);colors.extend([color]*len(v))
    poly=Poly3DCollection(np.concatenate(meshes),facecolors=colors,shade=True,zsort='average');ax.add_collection3d(poly)
    ax.set(xlim=(-17,17),ylim=(-35,35),zlim=(0,44 if exploded else 14))
    ax.set_box_aspect((34,70,44 if exploded else 14));ax.view_init(28,-58);ax.set_axis_off()
    fig.text(.08,.91,'ASM2464PD / REV-A',fontsize=24,weight='bold',color='#233344')
    fig.text(.08,.865,'Exploded assembly' if exploded else 'Assembled enclosure',fontsize=17,color='#42596b')
    fig.text(.08,.80,'Copper: exposed thermal insert\nGold: service connector envelope\nRed: four removable SPI isolation shunts\nGreen / blue: controller PCB / M.2 2230',fontsize=11,color='#42596b',linespacing=1.6,va='top')
    fig.text(.08,.07,'PROVISIONAL FIT MODEL  •  ASSUMED COMPONENT HEIGHTS\nCPU firmware boot → deliberate brick → external recovery: verified in simulation',fontsize=10,color='#42596b',linespacing=1.7)
    fig.subplots_adjust(left=.08,right=.99,bottom=.13,top=.80)
    fig.savefig(OUT/('exploded.png' if exploded else 'assembled.png'),dpi=150,facecolor=fig.get_facecolor());plt.close(fig)
if __name__=='__main__':render(False);render(True)
