"""仅由 Blender --background 执行的 FBX 求值脚本；不导入训练环境。"""

import json
import sys
from pathlib import Path


def main():
    # 此文件由 Blender 自带 Python 执行，训练虚拟环境不安装 bpy。
    import bpy  # pyright: ignore[reportMissingImports]

    arguments = sys.argv[sys.argv.index("--") + 1:]
    source, output = map(Path, arguments[:2])
    bpy.ops.import_scene.fbx(filepath=str(source), use_anim=True, automatic_bone_orientation=False)
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    if len(armatures) != 1:
        raise ValueError("首版 FBX 需要恰好一个 Armature")
    armature = armatures[0]
    actions = list(bpy.data.actions)
    if len(arguments) == 3:
        action = bpy.data.actions.get(arguments[2])
        if action is None:
            raise ValueError("指定的 FBX Action 不存在")
    elif len(actions) == 1:
        action = actions[0]
    else:
        raise ValueError("FBX 有多个 Action，必须指定 animation_id")
    armature.animation_data_create()
    armature.animation_data.action = action
    if hasattr(action, "slots") and len(action.slots):
        slots = [slot for slot in action.slots if slot.target_id_type == "OBJECT"]
        if len(slots) != 1:
            raise ValueError("FBX Action slot 不唯一")
        armature.animation_data.action_slot = slots[0]
    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    start, end = action.frame_range
    frames = [start + i for i in range(int(end - start) + 1)]
    bones = []

    def add(bone):
        if bone in bones:
            return
        if bone.parent:
            add(bone.parent)
        bones.append(bone)

    for bone in armature.data.bones:
        add(bone)

    def values(matrix):
        position, quaternion, scale = matrix.decompose()
        reconstructed = matrix.__class__.LocRotScale(position, quaternion, scale)
        if any(abs(matrix[row][column] - reconstructed[row][column]) > 1e-5 for row in range(4) for column in range(4)):
            raise ValueError("FBX 变换包含无法以 TRS 表达的 shear")
        return list(position), [quaternion.x, quaternion.y, quaternion.z, quaternion.w], list(scale)

    rest = [values(bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent
                   else armature.matrix_world @ bone.matrix_local) for bone in bones]
    tracks = [{"positions": [], "rotations": [], "scales": []} for _ in bones]
    for frame in frames:
        integer = int(frame)
        scene.frame_set(integer, subframe=frame - integer)
        for index, bone in enumerate(bones):
            pose = armature.pose.bones[bone.name]
            local = pose.parent.matrix.inverted() @ pose.matrix if pose.parent else armature.matrix_world @ pose.matrix
            position, rotation, scale = values(local)
            tracks[index]["positions"].append(position)
            tracks[index]["rotations"].append(rotation)
            tracks[index]["scales"].append(scale)
    data = {"skeleton": {"names": [bone.name for bone in bones], "parents": [bones.index(bone.parent) if bone.parent else -1 for bone in bones],
                         "rest_positions": [value[0] for value in rest], "rest_rotations": [value[1] for value in rest],
                         "rest_scales": [value[2] for value in rest]},
            "timestamps": [(frame - start) / fps for frame in frames], "tracks": tracks,
            "action": action.name, "fps": fps, "meters_per_unit": scene.unit_settings.scale_length,
            "blender_version": bpy.app.version_string}
    output.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
