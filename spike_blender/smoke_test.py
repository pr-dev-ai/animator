"""Headless EEVEE smoke test: does Blender 5.2 render a non-empty frame on this box?"""
import bpy, sys, os

out = sys.argv[sys.argv.index("--") + 1]

# Clean slate
bpy.ops.wm.read_factory_settings(use_empty=True)

scene = bpy.context.scene
# NB: 5.x renamed the EEVEE-Next enum back to plain "BLENDER_EEVEE".
# 4.2-4.5 used "BLENDER_EEVEE_NEXT", which 5.2 rejects outright.
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = 320
scene.render.resolution_y = 240
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = out

# A plane, a light, a camera pointed at it.
bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 0, 0))
plane = bpy.context.active_object
mat = bpy.data.materials.new("M")
mat.use_nodes = True
mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (1, 0.2, 0.1, 1)
plane.data.materials.append(mat)

bpy.ops.object.light_add(type="SUN", location=(0, 0, 5))
bpy.context.active_object.data.energy = 5

bpy.ops.object.camera_add(location=(0, 0, 5), rotation=(0, 0, 0))
scene.camera = bpy.context.active_object

bpy.ops.render.render(write_still=True)
print("SMOKE_RENDER_WROTE:", out, os.path.exists(out), os.path.getsize(out) if os.path.exists(out) else -1)
