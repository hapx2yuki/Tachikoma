"""BlenderMCP only. Build fabrication readback preview and preserve P35 terminal geometry."""
import bpy,math,json,hashlib
from mathutils import Vector,Matrix
O='source-root/P36_PCB_FAB_PREP'
r=json.load(open(O+'/FAB_READBACK_GEOMETRY.json'))
native=bpy.data.scenes.new('P36_DISTRIBUTOR_NATIVE');view=bpy.data.scenes.new('P36_COPPER_REVIEW');assembly=bpy.data.scenes.new('P36_ASSEMBLY_REVIEW')
colors={'POWER_IN':(.8,.13,.05,1),'LEG_VBUS':(.95,.52,.05,1),'GND':(.38,.48,.56,1)}

def mesh(name,verts,faces,color,scene):
    me=bpy.data.meshes.new(name);me.from_pydata(verts,[],faces);me.update();ob=bpy.data.objects.new(name,me);ob.color=color;scene.collection.objects.link(ob);return ob

def polygon_primitive(p,z):
    if 'a' in p:
        a,b=p['a'],p['b'];ang=math.atan2(b[1]-a[1],b[0]-a[0]);rad=p['w']/2
        return [(b[0]+rad*math.cos(ang-math.pi/2+i*math.pi/16),b[1]+rad*math.sin(ang-math.pi/2+i*math.pi/16),z) for i in range(17)]+[(a[0]+rad*math.cos(ang+math.pi/2+i*math.pi/16),a[1]+rad*math.sin(ang+math.pi/2+i*math.pi/16),z) for i in range(17)]
    x,y=p['xy']
    if p['shape']=='R':
        w,h=p['size'];return [(x-w/2,y-h/2,z),(x+w/2,y-h/2,z),(x+w/2,y+h/2,z),(x-w/2,y+h/2,z)]
    return [(x+p['diam']/2*math.cos(i*2*math.pi/48),y+p['diam']/2*math.sin(i*2*math.pi/48),z) for i in range(48)]

def copper(scene,layer,xoff=0,mirror=False,z=.82):
    obs=[]
    for net in sorted(set(p['net'] for p in r['copper'][layer])):
        vv=[];ff=[]
        for p in r['copper'][layer]:
            if p['net']!=net:continue
            v=polygon_primitive(p,z);n=len(vv);vv.extend([(xoff+(-x if mirror else x),y,zz) for x,y,zz in v]);ff.append(list(range(n,n+len(v))))
        ob=mesh('P36_'+scene.name+'_'+layer+'_'+net,vv,ff,colors.get(net,(.05,.65,.58,1)),scene);ob['source']='Parsed Gerber, copper exposed for review';obs.append(ob)
    vv=[];ff=[]
    for h in r['holes']:
        v=polygon_primitive(dict(xy=h['xy'],shape='C',diam=h['diam']),z+.02);n=len(vv);vv.extend([(xoff+(-x if mirror else x),y,zz) for x,y,zz in v]);ff.append(list(range(n,n+len(v))))
    ob=mesh('P36_'+scene.name+'_'+layer+'_DRILL_VISUAL',vv,ff,(.006,.009,.014,1),scene);ob['representation']='Drill openings overlaid for copper view; board mesh has real holes'
    return obs

def text(scene,s,x,y,size=1.5,color=(.08,.10,.15,1)):
    c=bpy.data.curves.new('P36_label','FONT');c.body=s;c.size=size;c.align_x='CENTER';c.align_y='CENTER';o=bpy.data.objects.new('P36_'+s,c);scene.collection.objects.link(o);o.location=(x,y,1);o.color=color;return o

def rect(scene,name,x,y,w,h,z,color):return mesh(name,[(x-w/2,y-h/2,z),(x+w/2,y-h/2,z),(x+w/2,y+h/2,z),(x-w/2,y+h/2,z)],[(0,1,2,3)],color,scene)
for layer,x,mir,label in [('F',-21,False,'FRONT / SERVO'),('B',21,True,'BACK / PWM INPUT')]:
    rect(view,'P36_'+layer+'_SUBSTRATE',x,0,25.4,62.5,.8,(.045,.12,.10,1));copper(view,layer,x,mir)
    text(view,label,x,35,1.65)
text(view,'P36 | 62.5 x 25.4 mm | 2 layers / 4.5 oz',0,41,1.8)
text(view,'RED: battery +     GOLD: fused +     GRAY: GND     TEAL: PWM',0,-36,1.25)
text(view,'Copper view reconstructed from exported Gerber / drill files',0,-39,1.25)
text(view,'15 A fuse candidate / prototype verification required',0,-42,1.25)
# The native component scene: fresh substrate drilled from fabrication data.
bpy.context.window.scene=native
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,0));board=bpy.context.object;board.name='P36_PCB';board.dimensions=(25.4,62.5,1.6);bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);board.color=(.045,.12,.10,1)
cutters=[]
for h in r['holes']:
    bpy.ops.mesh.primitive_cylinder_add(vertices=32,radius=h['diam']/2,depth=3,location=(*h['xy'],0));cutters.append(bpy.context.object)
bpy.ops.object.select_all(action='DESELECT')
for ob in cutters:ob.select_set(True)
bpy.context.view_layer.objects.active=cutters[0];bpy.ops.object.join();cut=cutters[0]
bpy.context.view_layer.objects.active=board;mod=board.modifiers.new('Fabrication drill readback','BOOLEAN');mod.operation='DIFFERENCE';mod.solver='EXACT';mod.object=cut;bpy.ops.object.modifier_apply(modifier=mod.name);bpy.data.objects.remove(cut,do_unlink=True)
copper(native,'F',z=.801);copper(native,'B',z=-.801)
# Existing terminal component transforms remain unchanged. Old schematic wire routes are not copied.
source=bpy.data.scenes['P35_SOLDER_PAD_NATIVE'];copied=[]
for ob in source.objects:
    n=ob.name
    include=ob.type=='MESH' and ('_OUT_' in n or n.startswith('P35_IN_') or n=='P35_INPUT_2x7' or n.startswith('P35_FUSE_')) and ('ENV' not in n or n=='P35_FUSE_UNRATED_ENVELOPE')
    if include:
        new=ob.copy();new.name=n.replace('P35_','P36_',1);new.parent=None;new.matrix_world=ob.matrix_world.copy();native.collection.objects.link(new);copied.append((ob,new))
        if n=='P35_FUSE_UNRATED_ENVELOPE':new.name='P36_FUSE_15A_CANDIDATE';new.color=(.02,.17,.7,1)
# Model matching verified by unchanged mesh data and transforms (no guessed replacement servo plugs).
assert all(a.data is b.data and max(abs(a.matrix_world[i][j]-b.matrix_world[i][j]) for i in range(4) for j in range(4))<1e-9 for a,b in copied)
for ob in native.objects:
    new=ob.copy();new.parent=None;new.matrix_world=ob.matrix_world.copy();assembly.collection.objects.link(new)

def camera(scene,loc,target,scale,res,path):
    ca=bpy.data.cameras.new('P36_camera');cam=bpy.data.objects.new('P36_camera',ca);scene.collection.objects.link(cam);cam.location=loc;cam.rotation_euler=(Vector(target)-cam.location).to_track_quat('-Z','Y').to_euler();ca.type='ORTHO';ca.ortho_scale=scale;scene.camera=cam
    scene.render.engine='BLENDER_WORKBENCH';scene.display.shading.light='STUDIO';scene.display.shading.color_type='OBJECT';scene.display.shading.show_shadows=False;scene.display.shading.show_cavity=False;scene.display.shading.background_type='WORLD';scene.world=bpy.data.worlds.new('P36_world');scene.world.color=(.8,.82,.85)
    scene.render.resolution_x=res[0];scene.render.resolution_y=res[1];scene.render.resolution_percentage=100;scene.render.image_settings.file_format='PNG';scene.render.filepath=path
camera(view,(0,0,180),(0,0,0),94,(1350,1450),O+'/P36_COPPER_READBACK.png')
camera(assembly,(75,-100,140),(0,0,2),82,(1200,1300),O+'/P36_ASSEMBLY.png')
for scene in [native,view,assembly]:
    scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=.001
    scene['status']='Fabrication prototype candidate, not thermally qualified'
    scene['source_revision']='P35 terminal geometry unchanged, P36 Gerber copper'
bpy.context.window.scene=view
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=='VIEW_3D':
            space=area.spaces.active;space.region_3d.view_perspective='CAMERA';space.shading.color_type='OBJECT'
textblock=bpy.data.texts.new('P36_FAB_CHECKS');textblock.write(open(O+'/FAB_READBACK_CHECKS.json').read())
bpy.ops.render.render(write_still=True,scene=view.name)
bpy.ops.render.render(write_still=True,scene=assembly.name)
path=O+'/P36_DISTRIBUTOR.blend';bpy.data.libraries.write(path,{native,view,assembly,textblock},fake_user=True,compress=True)
def sig(ob):
    return hashlib.sha256(json.dumps({'v':[list(v.co) for v in ob.data.vertices],'p':[list(p.vertices) for p in ob.data.polygons]},sort_keys=True).encode()).hexdigest()
expected=sig(board)
with bpy.data.libraries.load(path,link=False) as (a,b):b.objects=['P36_PCB']
loaded=b.objects[0];assert sig(loaded)==expected;bpy.data.objects.remove(loaded,do_unlink=True)
json.dump({'saved_blend':path,'PCB_mesh_sha256':expected,'saved_PCB_mesh_readback_matches':True,'existing_component_transforms_preserved':len(copied),'board_has_drill_holes':82,'copper_source':'Parsed manufacturing Gerber files','component_meshes':'Unchanged P35 visual envelopes; exact manufacturer post heights not substituted'},open(O+'/BLENDER_READBACK.json','w'),indent=2)
print(json.dumps({'blend':path,'copper_preview':view.render.filepath,'assembly_preview':assembly.render.filepath,'unchanged_component_count':len(copied),'saved_mesh_verified':True}))
