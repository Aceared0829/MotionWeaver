"""Python/.NET 共用交换格式：motion.json 与小端类型数组 arrays.bin。元数据最后写入。"""

import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Literal, overload

import numpy as np

from data.runtime.native_motion import (
    NativeMotion,
    NativeSkeleton,
    NativeTrack,
    SampledMotion,
    SourceInfo,
    SourceSpace,
    validate_native_motion,
    validate_sampled_motion,
)

SCHEMA = "motionweaver_motion"
VERSION = 1


def skeleton_signature(skeleton: NativeSkeleton) -> str:
    digest = hashlib.sha256(json.dumps(skeleton.names, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    for values, dtype in ((skeleton.parents, "<i8"), (skeleton.rest_positions, "<f8"),
                          (skeleton.rest_rotations, "<f8"), (skeleton.rest_scales, "<f8")):
        digest.update(np.asarray(values, dtype=dtype).tobytes())
    return digest.hexdigest()


def _write_motion(folder, motion, skeleton_store=None):
    native = isinstance(motion, NativeMotion)
    (validate_native_motion if native else validate_sampled_motion)(motion)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    skeleton = motion.skeleton
    shared = None
    if skeleton_store is not None:
        shared = Path(skeleton_store) / skeleton_signature(skeleton)
        if not shared.exists():
            shared.mkdir(parents=True)
            fields: dict[str, object] = {"names": skeleton.names}
            with (shared / "arrays.bin").open("wb") as skeleton_stream:
                for name, dtype in (("parents", "<i8"), ("rest_positions", "<f8"), ("rest_rotations", "<f8"), ("rest_scales", "<f8")):
                    values = np.asarray(getattr(skeleton, name), dtype=dtype)
                    fields[name] = {"offset": skeleton_stream.tell(), "shape": list(values.shape), "dtype": values.dtype.str}
                    skeleton_stream.write(values.tobytes())
            (shared / "skeleton.json").write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
        elif not (shared / "skeleton.json").is_file():
            raise ValueError("共享骨架未完整写入")
    with (folder / "arrays.bin").open("wb", buffering=1024 * 1024) as stream:
        def array(values, dtype="<f8"):
            values = np.asarray(values, dtype=dtype)
            descriptor = {"offset": stream.tell(), "shape": list(values.shape), "dtype": values.dtype.str}
            stream.write(values.tobytes(order="C"))
            return descriptor

        document = {"schema": SCHEMA, "version": VERSION, "kind": "native" if native else "sampled",
                    "source": asdict(motion.source), "root_joint": motion.root_joint}
        if shared is None:
            document["skeleton"] = {"names": skeleton.names, "parents": array(skeleton.parents, "<i8"),
                                 "rest_positions": array(skeleton.rest_positions),
                                 "rest_rotations": array(skeleton.rest_rotations), "rest_scales": array(skeleton.rest_scales)}
        else:
            reference = Path(os.path.relpath(shared, folder)).as_posix()
            if not re.fullmatch(r"\.\./\.\./skeletons/[a-f0-9]{64}", reference):
                raise ValueError("共享布局必须为同一数据版本下的 skeletons/ 与 clips/")
            document["skeleton_ref"] = reference
        if native:
            document.update(duration=motion.duration, additive=motion.additive,
                            space={"meters_per_unit": motion.space.meters_per_unit, "basis": motion.space.basis.tolist()})
            tracks = []
            for track in motion.tracks:
                item: dict[str, object] = {"joint_index": track.joint_index}
                for field in ("position_times", "positions", "rotation_times", "rotations", "scale_times", "scales"):
                    item[field] = array(getattr(track, field))
                for channel in ("position", "rotation", "scale"):
                    item[channel + "_interpolation"] = getattr(track, channel + "_interpolation")
                tracks.append(item)
            document["tracks"] = tracks
        else:
            document.update(source_duration=motion.source_duration, fps=motion.fps,
                            space={"meters_per_unit": 1, "basis": np.eye(3).tolist()},
                            timestamps=array(motion.timestamps), positions=array(motion.positions),
                            rotations=array(motion.rotations), scales=array(motion.scales),
                            track_mask=array(motion.track_mask, "|b1"))
    (folder / "motion.json").write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_native_motion(folder, motion: NativeMotion, skeleton_store=None) -> None:
    _write_motion(folder, motion, skeleton_store)


def write_sampled_motion(folder, motion: SampledMotion, skeleton_store=None) -> None:
    _write_motion(folder, motion, skeleton_store)


@overload
def _read_motion(folder, kind: Literal["native"], file_hashes=None) -> NativeMotion: ...


@overload
def _read_motion(folder, kind: Literal["sampled"], file_hashes=None) -> SampledMotion: ...


def _read_motion(folder, kind: Literal["native", "sampled"], file_hashes=None) -> NativeMotion | SampledMotion:
    folder = Path(folder)
    metadata = (folder / "motion.json").read_bytes()
    document = json.loads(metadata.decode("utf-8"))
    if (document.get("schema"), document.get("version"), document.get("kind")) != (SCHEMA, VERSION, kind):
        raise ValueError("不支持的运动交换契约")
    binary = folder / "arrays.bin"
    buffers = {binary: binary.read_bytes()}
    # 回执使用实际回读的字节，避免调用者再次打开大文件计算同一哈希。
    if file_hashes is not None:
        file_hashes.update({"motion.json": hashlib.sha256(metadata).hexdigest(),
                            "arrays.bin": hashlib.sha256(buffers[binary]).hexdigest()})

    def array(descriptor, data_file=binary):
        if descriptor["dtype"] not in ("<f8", "<i8", "|b1"):
            raise ValueError("不支持的数组数据类型")
        shape, offset = descriptor["shape"], descriptor["offset"]
        if not shape or any(not isinstance(n, int) or n < 0 for n in shape) or not isinstance(offset, int) or offset < 0:
            raise ValueError("无效数组维数或偏移")
        count = 1
        for n in shape:
            count *= n
        dtype = np.dtype(descriptor["dtype"])
        if data_file not in buffers:
            buffers[data_file] = data_file.read_bytes()
        data = buffers[data_file]
        if offset + count * dtype.itemsize > len(data):
            raise ValueError("轨道二进制文件被截断")
        return np.frombuffer(data, dtype=dtype, count=count, offset=offset).reshape(shape).copy()

    skeleton_binary = binary
    if "skeleton_ref" in document:
        reference = document["skeleton_ref"]
        if not re.fullmatch(r"\.\./\.\./skeletons/[a-f0-9]{64}", reference):
            raise ValueError("无效共享骨架引用")
        shared = (folder / reference).resolve()
        if not shared.is_relative_to(folder.resolve().parent.parent):
            raise ValueError("共享骨架路径越界")
        fields = json.loads((shared / "skeleton.json").read_text(encoding="utf-8"))
        skeleton_binary = shared / "arrays.bin"
    else:
        fields = document["skeleton"]
    skeleton = NativeSkeleton(fields["names"], array(fields["parents"], skeleton_binary), array(fields["rest_positions"], skeleton_binary),
                              array(fields["rest_rotations"], skeleton_binary), array(fields["rest_scales"], skeleton_binary))
    if "skeleton_ref" in document and skeleton_signature(skeleton) != Path(document["skeleton_ref"]).name:
        raise ValueError("共享骨架内容与签名不一致")
    source = SourceInfo(**document["source"])
    if kind == "native":
        tracks = []
        for item in document["tracks"]:
            values = {name: array(item[name]) for name in ("position_times", "positions", "rotation_times", "rotations", "scale_times", "scales")}
            tracks.append(NativeTrack(item["joint_index"], **values,
                                      **{c + "_interpolation": item[c + "_interpolation"] for c in ("position", "rotation", "scale")}))
        space = document["space"]
        motion = NativeMotion(skeleton, tracks, document["duration"], SourceSpace(space["meters_per_unit"], np.asarray(space["basis"], dtype=float)),
                              source, document["root_joint"], document["additive"])
        validate_native_motion(motion)
    else:
        if document["space"] != {"meters_per_unit": 1, "basis": np.eye(3).tolist()}:
            raise ValueError("标准化运动必须使用米制 Motion 空间")
        motion = SampledMotion(skeleton, array(document["timestamps"]), array(document["positions"]), array(document["rotations"]),
                               array(document["scales"]), array(document["track_mask"]), source, document["source_duration"],
                               document["fps"], document["root_joint"])
        validate_sampled_motion(motion)
    return motion


def read_native_motion(folder, *, file_hashes=None) -> NativeMotion:
    return _read_motion(folder, "native", file_hashes)


def read_sampled_motion(folder, *, file_hashes=None) -> SampledMotion:
    return _read_motion(folder, "sampled", file_hashes)
