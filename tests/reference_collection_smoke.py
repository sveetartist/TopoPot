"""Run in Blender background mode to check the reference collection button."""

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import topopot_retopo_tool as retopo_tool


retopo_tool.register()
scene = bpy.context.scene
for obj in bpy.context.selected_objects:
    obj.select_set(False)

mesh = bpy.data.meshes.new("Reference Mesh")
model = bpy.data.objects.new("Reference Model", mesh)
scene.collection.objects.link(model)
extra = bpy.data.collections.new("Previous Collection")
scene.collection.children.link(extra)
extra.objects.link(model)
model.select_set(True)

assert bpy.ops.mesh.retopo_tool_add_reference.poll()
assert bpy.ops.mesh.retopo_tool_add_reference() == {'FINISHED'}
collection = scene.retopo_tool.target
assert collection.name == retopo_tool.REFERENCE_COLLECTION_NAME
assert collection in scene.collection.children_recursive
assert model.name in collection.objects
assert tuple(model.users_collection) == (collection,)

second_mesh = bpy.data.meshes.new("Second Reference Mesh")
second_model = bpy.data.objects.new("Second Reference Model", second_mesh)
scene.collection.objects.link(second_model)
second_model.select_set(True)
assert bpy.ops.mesh.retopo_tool_add_reference() == {'FINISHED'}
assert scene.retopo_tool.target == collection
assert {obj.name for obj in collection.objects} == {model.name, second_model.name}
assert tuple(second_model.users_collection) == (collection,)

custom = bpy.data.collections.new("Custom Reference")
scene.retopo_tool.target = custom
assert bpy.ops.mesh.retopo_tool_add_reference() == {'FINISHED'}
assert scene.retopo_tool.target == custom
assert custom in scene.collection.children_recursive
assert {obj.name for obj in custom.objects} == {model.name, second_model.name}
assert tuple(model.users_collection) == (custom,)
assert tuple(second_model.users_collection) == (custom,)

print("Reference collection smoke test passed")
