#!/usr/bin/env python3
"""追加購入を抑える幅広シャーシ・頭受け・開放電装棚・Cabin受け。

原型/既存STLを上書きしない。config.PRINT_FIRSTの寸法と実メッシュで生成。
Cabinの中実印刷物を空洞として使わず、外側から荷重を受け、既存面ファスナーで保持。
"""
import json,sys,hashlib
from pathlib import Path
import numpy as np,trimesh
from manifold3d import Manifold,Mesh,OpType
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'hardware/src')]
import config as C
import export_urdf as E
import make_chassis as MC
import make_ld220_adapter as D
import make_print_first_leg as PL
from lib import box,cyl,rbox,to_trimesh
import print_first_assembly as A
OUT=A.OUT

def native(m):
    return Manifold(Mesh(np.asarray(m.vertices,dtype=np.float32),np.asarray(m.faces,dtype=np.uint32)))

def bounded(lo,hi):return box(*(np.asarray(hi)-lo)).translate((np.asarray(hi)+lo)/2)


CLEARANCE_MM = 0.20
INTERSECTION_THRESHOLD_MM3 = 0.01


def _native_checked(shape, label):
    """入力形状を閉じた正体積へ限定してManifold化する。"""
    if isinstance(shape, Manifold):
        result = shape
    elif isinstance(shape, trimesh.Trimesh):
        if len(shape.vertices) < 4 or len(shape.faces) < 4:
            raise ValueError(f'{label}: empty mesh')
        if (not np.isfinite(shape.vertices).all()
                or not np.isfinite(shape.faces).all()):
            raise ValueError(f'{label}: non-finite mesh data')
        if np.any(shape.faces < 0) or np.any(shape.faces >= len(shape.vertices)):
            raise ValueError(f'{label}: out-of-range face index')
        if not shape.is_watertight or not shape.is_winding_consistent:
            raise ValueError(f'{label}: mesh is not watertight/consistently wound')
        if not np.isfinite(shape.volume) or shape.volume <= 0:
            raise ValueError(f'{label}: mesh volume is not positive')
        result = native(shape)
    else:
        raise TypeError(f'{label}: unsupported shape type {type(shape)!r}')
    status = result.status()
    if status.name != 'NoError':
        raise ValueError(f'{label}: Manifold status={status}')
    volume = float(result.volume())
    if not np.isfinite(volume) or volume <= 0:
        raise ValueError(f'{label}: Manifold volume is not positive: {volume!r}')
    return result


def _intersection_mm3(a, b, label):
    """厳格なManifold交差体積を返す。"""
    result = a ^ b
    status = result.status()
    if status.name != 'NoError':
        raise ValueError(f'{label}: intersection status={status}')
    value = float(result.volume())
    if not np.isfinite(value) or value < -1.0e-6:
        raise ValueError(f'{label}: invalid intersection volume {value!r}')
    return max(0.0, value)


def _solid_component_report(shape, label, *, require_one=False):
    """Manifold分解結果の有限性・符号・一体性を検査する。"""
    components = list(shape.decompose())
    volumes = [float(part.volume()) for part in components]
    if any(not np.isfinite(volume) for volume in volumes):
        raise ValueError(f'{label}: non-finite component volume')
    negative = [volume for volume in volumes if volume < -1.0e-8]
    if negative:
        raise ValueError(f'{label}: negative component volume(s): {negative}')
    positive = [part for part, volume in zip(components, volumes)
                if volume > 1.0e-8]
    if require_one and len(positive) != 1:
        raise ValueError(f'{label}: expected one positive component, got {len(positive)}')
    if not positive:
        raise ValueError(f'{label}: no positive component')
    return components, volumes, positive


def _clearance_difference(base, obstacles, *, label, clearance=CLEARANCE_MM):
    """障害物を指定公差で逃がし、最大の閉じた正体積成分を返す。

    複数成分が生じた場合は、採用した最大成分と廃棄成分を台帳へ記録する。
    廃棄を無言で「一体化」と扱わず、廃棄体積が大きすぎる形状は停止する。
    """
    clearance = float(clearance)
    if not np.isfinite(clearance) or clearance <= 0.0:
        raise ValueError(f'{label}: invalid clearance {clearance!r}')
    current = _native_checked(base, f'{label}/base')
    initial_volume = float(current.volume())
    expanded_box = box(2.0 * clearance, 2.0 * clearance, 2.0 * clearance)
    prepared = []
    subtraction_rows = []
    for obstacle_label, obstacle in obstacles:
        obstacle_native = _native_checked(obstacle, f'{label}/{obstacle_label}')
        raw_before = _intersection_mm3(
            current, obstacle_native, f'{label}/{obstacle_label}/before')
        expanded = obstacle_native.minkowski_sum(expanded_box)
        expanded = _native_checked(expanded, f'{label}/{obstacle_label}/expanded')
        current = current - expanded
        status = current.status()
        if status.name != 'NoError':
            raise ValueError(f'{label}/{obstacle_label}: subtraction status={status}')
        if not np.isfinite(current.volume()) or current.volume() <= 0.0:
            raise ValueError(f'{label}/{obstacle_label}: subtraction removed all material')
        prepared.append((obstacle_label, obstacle_native))
        subtraction_rows.append({
            'obstacle': obstacle_label,
            'clearance_each_side_mm': clearance,
            'base_intersection_before_mm3': raw_before,
            'expanded_obstacle_volume_mm3': float(expanded.volume()),
            'remaining_volume_after_mm3': float(current.volume()),
        })

    components, volumes, positive = _solid_component_report(
        current, f'{label}/result')
    selected_index = max(
        range(len(positive)), key=lambda index: float(positive[index].volume()))
    selected = positive[selected_index]
    discarded = [float(part.volume()) for index, part in enumerate(positive)
                 if index != selected_index]
    positive_component_geometry = []
    for index, part in enumerate(positive):
        part_mesh = to_trimesh(part.simplify(.005))
        positive_component_geometry.append({
            'positive_component_index': index,
            'volume_mm3': float(part.volume()),
            'selected': index == selected_index,
            'bounds_mm': part_mesh.bounds.astype(float).tolist(),
        })
    selected_volume = float(selected.volume())
    discarded_volume = float(sum(discarded))
    if discarded_volume > max(1.0, selected_volume * 0.02):
        raise ValueError(
            f'{label}: clearance cut discarded too much disconnected material: '
            f'{discarded_volume} mm3 from {selected_volume} mm3')
    final_intersections = []
    for obstacle_label, obstacle_native in prepared:
        value = _intersection_mm3(
            selected, obstacle_native, f'{label}/{obstacle_label}/final')
        final_intersections.append({
            'obstacle': obstacle_label,
            'intersection_mm3': value,
            'status': 'PASS' if value <= INTERSECTION_THRESHOLD_MM3 else 'FAIL',
        })
        if value > INTERSECTION_THRESHOLD_MM3:
            raise ValueError(
                f'{label}/{obstacle_label}: final material intersection {value} mm3')
    selected_mesh = to_trimesh(selected.simplify(.005))
    if (not selected_mesh.is_watertight
            or not selected_mesh.is_winding_consistent
            or not np.isfinite(selected_mesh.volume)
            or selected_mesh.volume <= 0.0
            or not selected_mesh.is_volume):
        raise ValueError(f'{label}: selected result is not a serialized solid candidate')
    return selected, {
        'status': 'PASS',
        'clearance_each_side_mm': clearance,
        'base_volume_mm3': initial_volume,
        'result_volume_before_component_selection_mm3': float(current.volume()),
        'result_volume_selected_mm3': selected_volume,
        'result_component_count_before_selection': len(components),
        'positive_component_count_before_selection': len(positive),
        'discarded_disconnected_component_count': len(discarded),
        'discarded_disconnected_component_volumes_mm3': discarded,
        'discarded_disconnected_volume_mm3': discarded_volume,
        'positive_component_geometry': positive_component_geometry,
        'component_selection_rule': 'largest_positive_finite_component; discarded components are recorded explicitly',
        'subtractions': subtraction_rows,
        'final_raw_obstacle_intersections': final_intersections,
        'serialized_candidate': {
            'watertight': bool(selected_mesh.is_watertight),
            'winding_consistent': bool(selected_mesh.is_winding_consistent),
            'is_volume': bool(selected_mesh.is_volume),
            'positive_volume_mm3': float(selected_mesh.volume),
        },
    }


def _servo_case_mesh(profile, frame):
    """sim_collision と同じ servo case の実占有箱を返す。"""
    cx = float(profile['L']) / 2.0 - float(profile['SHAFT_OFF'])
    mesh = trimesh.creation.box((profile['L'], profile['W'], profile['TAB_BELOW']))
    mesh.apply_transform(frame @ E.trans(-cx, 0.0, -float(profile['TAB_BELOW']) / 2.0))
    return mesh


def _head_clearance_obstacles(head, carrier, holder=None):
    """頭殻と可動ケース/最終carrierを同じbody座標で比較する。"""
    obstacles = []
    for index, frame in ((0, E.eye_servo_frame(0)), (2, E.eye_servo_frame(2))):
        obstacles.append((f"eye_{'r' if index == 0 else 'l'}_servo_case",
                          _servo_case_mesh(C.EYE_SERVO, frame)))
    for side in ('r', 'l'):
        frame = E.arm_servo_frames(side)['yaw']
        obstacles.append((f'arm_{side}_yaw_servo_case',
                          _servo_case_mesh(C.ARM_SERVO, frame)))
    candidate = D.Candidate()
    occupied = D.case_envelope(candidate) + D.cable_reservation(candidate)
    occupied = occupied.translate([0.0, 0.0, -candidate.main_projection])
    for leg in E.LEGS:
        frame = PL.print_first_leg_frames(leg)['yaw']
        obstacles.append((f'leg_{leg.lower()}_yaw_ld220_case',
                          to_trimesh(PL._xform(occupied, frame))))
    # ``_build_pf_camera_carrier`` returns a Manifold, while audit callers may
    # pass the serialized replacement STL back as a Trimesh.  Normalize both
    # forms before applying the camera frame.
    carrier_body = (to_trimesh(carrier)
                    if isinstance(carrier, Manifold) else carrier.copy())
    carrier_body.apply_transform(E.camera_mount_frame({}))
    obstacles.append(('pf_camera_carrier', carrier_body))
    # The board and the fixed camera lens are also checked against the shell;
    # the lens is never translated by the XIAO board lift.
    holder = A.xiao_holder_meshes() if holder is None else holder
    for name in ('xiao_all_boards_occupancy', 'camera_child_lens_occupancy'):
        fixed = holder[name].copy()
        fixed.apply_transform(E.camera_mount_frame({}))
        obstacles.append((name, fixed))
    for name, obstacle in obstacles:
        obstacle.apply_translation([0.0, 0.0, -E.ZB])
    return obstacles


def _build_pf_camera_carrier(head):
    """頭殻/XIAO基板を逃がした最終camera carrierと監査値を作る。"""
    carrier = E.camera_link_parts()[1][0]
    head_at_camera = head.copy()
    head_at_camera.apply_translation([0.0, 0.0, E.ZB])
    head_at_camera.apply_transform(np.linalg.inv(E.camera_mount_frame({})))
    holder = A.xiao_holder_meshes()
    board = holder['xiao_all_boards_occupancy']
    fitted = native(carrier)
    fitted -= native(head_at_camera).minkowski_sum(box(.4, .4, .4))
    fitted -= native(board).minkowski_sum(box(.4, .4, .4))
    if fitted.status().name != 'NoError' or fitted.volume() <= 0.0:
        raise ValueError('pf_camera_carrier initial clearance failed')
    carrier_intersections = []
    holder_intersections = []
    board_beam_intersections = []
    floor = native(holder['xiao_tray_floor_candidate'])
    for index, beam_shape in enumerate(A.xiao_connection_beams(), start=1):
        beam_native = native(beam_shape)
        carrier_overlap = _intersection_mm3(
            beam_native, fitted, f'pf_camera_carrier/beam{index}/carrier')
        floor_overlap = _intersection_mm3(
            beam_native, floor, f'pf_camera_carrier/beam{index}/floor')
        board_overlap = _intersection_mm3(
            beam_native, native(board), f'pf_camera_carrier/beam{index}/board')
        if carrier_overlap <= INTERSECTION_THRESHOLD_MM3 or floor_overlap <= INTERSECTION_THRESHOLD_MM3:
            raise ValueError(
                f'pf_camera_carrier beam{index} lacks positive carrier/floor overlap: '
                f'{carrier_overlap}/{floor_overlap}')
        if board_overlap > INTERSECTION_THRESHOLD_MM3:
            raise ValueError(
                f'pf_camera_carrier beam{index} intersects XIAO board: {board_overlap}')
        carrier_intersections.append(carrier_overlap)
        holder_intersections.append(floor_overlap)
        board_beam_intersections.append(board_overlap)
        fitted = Manifold.batch_boolean([fitted, beam_native], OpType.Add)
    holder_names = (
        'xiao_tray_floor_candidate',
        'xiao_tray_rib_left_candidate',
        'xiao_tray_rib_right_candidate',
    )
    holder_native = [native(holder[name]) for name in holder_names]
    fitted = Manifold.batch_boolean([fitted, *holder_native], OpType.Add)
    if fitted.status().name != 'NoError':
        raise ValueError(f'pf_camera_carrier final union failed: {fitted.status()}')
    components, _, positive = _solid_component_report(
        fitted, 'pf_camera_carrier/final_union', require_one=True)
    del components, positive
    board_final_overlap = _intersection_mm3(
        fitted, native(board), 'pf_camera_carrier/final_union/board')
    if board_final_overlap > INTERSECTION_THRESHOLD_MM3:
        raise ValueError(
            f'pf_camera_carrier final union intersects XIAO board: {board_final_overlap}')
    final_mesh = to_trimesh(fitted.simplify(.005))
    if (not final_mesh.is_watertight or not final_mesh.is_winding_consistent
            or not np.isfinite(final_mesh.volume) or final_mesh.volume <= 0.0
            or not final_mesh.is_volume):
        raise ValueError('pf_camera_carrier final union is not a valid solid')
    report = {
        'status': 'PASS',
        'clearance_each_side_mm': CLEARANCE_MM,
        'head_source_clearance_applied': True,
        'board_source_clearance_applied': True,
        'holder_parts_integrated': list(holder_names),
        'connection_beam_count': len(A.xiao_connection_beams()),
        'connection_beam_overlap_with_carrier_mm3': carrier_intersections,
        'connection_beam_overlap_with_tray_floor_mm3': holder_intersections,
        'connection_beam_overlap_with_board_mm3': board_beam_intersections,
        'direct_final_carrier_board_intersection_mm3': board_final_overlap,
        'serialized_union_components': 1,
        'serialized_union': {
            'watertight': bool(final_mesh.is_watertight),
            'winding_consistent': bool(final_mesh.is_winding_consistent),
            'is_volume': bool(final_mesh.is_volume),
            'volume_mm3': float(final_mesh.volume),
        },
        'board_nonintersection_status': 'PASS',
    }
    return fitted, holder, report

def beam(a,b,width=8):
    """矩形梁ではなく球端の凸包。新規生成の丸端なので外観元データは変えない。"""
    return Manifold.batch_hull([box(width,width,width).translate(a),box(width,width,width).translate(b)])

def chassis(head):
    f=C.PRINT_FIRST;t=f['frame_thickness'];p=C.YAW_SERVO
    body=cyl(t,2*f['central_radius']).translate([0,0,t/2])
    cx=p['L']/2-p['SHAFT_OFF'];cuts=Manifold()
    for leg,(x,y) in C.HIPS.items():
        a=f['yaw_case_angles'][leg];rad=np.radians(a);ca,sa=np.cos(rad),np.sin(rad)
        if C.PRINT_FIRST_ACTIVE:
            # LD-220MGは旧STDの耳穴へ落とし込まず、ケースを軸面から
            # +Zへ保持する開放ケージを採用する。body部品は後でZBだけ
            # 全体移動されるため、ここでは base_link 座標へ置く。
            raw = D.cradle(D.Candidate()).translate([0, 0, -D.Candidate().main_projection])
            yaw_T = (PL._translation(x, y, PL.yaw_face_z() - E.ZB)
                     @ PL._rotation(a, 'z') @ PL._rotation(180, 'x'))
            body += PL._xform(raw, yaw_T)
            # The case is a real occupied volume, not an empty hole.  Clear
            # it (including the cable reservation) from the old central plate
            # before adding the cage, with the same 0.3 mm candidate gap used
            # by make_ld220_adapter.  Without this cut the plate intersects
            # the physical LD body at the inner radial edge.
            pcase = D.Candidate()
            occupied = (D.case_envelope(pcase) + D.cable_reservation(pcase)).translate(
                [0, 0, -pcase.main_projection]).minkowski_sum(
                    box(*([2 * C.PRINT_FIRST.get('ld_case_fit_clear', pcase.gap)] * 3)))
            cuts += PL._xform(occupied, yaw_T)
            # 中央プレートからケージ内側へ短い三角梁を出す。ケージの
            # 4隅へ集中させず、ねじ/ケース壁へ荷重を分散する。
            ur=np.array([x,y],dtype=float);ur/=np.linalg.norm(ur)
            # Start the radial brace on the plate top (z=4 in this local
            # frame), rather than letting its 8 mm square section extend
            # 2 mm below the plate into the head-bottom shell.
            body += beam([*(ur*58.0), 4.0], [*(ur*72.0), 9.0], 8)
            # 既存シャーシの放射帯も残し、追加の購入部品を要求しない。
            body += rbox(abs(x)+5,20,t,r=2).rotate([0,0,a]).translate([x/2,y,t/2])
        else:
            sx=1 if x>0 else -1
            # 内側からケースの前後をつなぐ2本の帯。下側の可動腿を覆う円板を広げない。
            body+=rbox(62,30,t,r=3).rotate([0,0,a]).translate([x-cx*ca,y-cx*sa,t/2])
            body+=rbox(abs(x)+5,20,t,r=2).translate([x/2,y,t/2])
            cuts+=box(p['L']+.6,p['W']+.6,60).rotate([0,0,a]).translate([x-cx*ca,y-cx*sa,0])
            for hx in (-cx-p['HOLE_PITCH']/2,-cx+p['HOLE_PITCH']/2):
                for hy in (-p['HOLE_SPREAD']/2,p['HOLE_SPREAD']/2):
                    bx=x+hx*ca-hy*sa;by=y+hx*sa+hy*ca
                    body+=cyl(MC.BOSS_H+2,8).translate([bx,by,t+MC.BOSS_H/2-1])
                    cuts+=cyl(30,p['TAB_HOLE_D']).translate([bx,by,t/2])
    pa=C.ARM_SERVO;cxa=pa['L']/2-pa['SHAFT_OFF']
    arm_y_offset = float(f.get('arm_mount_y_offset', 0.0)) if C.PRINT_FIRST_ACTIVE else 0.0
    for sx in (-1,1):
        # Keep the printed chassis mount aligned with export_urdf.arm_mount_xy
        # and the arm servo collision envelope.  This is a world-Y shift of
        # the existing arm and hardware, not a new spacer or altered arm link.
        x,y=sx*C.ARM_MOUNT_XY[0],C.ARM_MOUNT_XY[1]+arm_y_offset
        body+=rbox(26,30,t,r=4).translate([x,y+1,t/2])
        for hy in (-cxa-pa['HOLE_PITCH']/2,-cxa+pa['HOLE_PITCH']/2):
            away=-1.5 if hy<-cxa else 1.5
            body+=rbox(8,7,C.ARM_BOSS_H+2,r=2).translate([x,y+hy+away,t+C.ARM_BOSS_H/2-1])
            cuts+=cyl(30,pa['TAB_HOLE_D']).translate([x,y+hy,t/2])
        cuts+=box(pa['W']+.6,pa['L']+.6,50).translate([x,y-cxa,0])
    for x,y in MC.CRADLE_BOLTS:cuts+=cyl(12,C.M3_TAP).translate([x,y,2])
    # Cabin支持・電装棚の共通基部。既存4本のM3×10皿ねじ候補を使う
    # （シャーシ4 mm + rail pad 6 mm、頭側から- Zへ挿入）。
    for sx in (-1,1):
        x=sx*f['cabin_rail_x']
        body+=rbox(18,48,t,r=3).translate([x,-70,t/2])
        for y in f['cabin_bolt_y']:cuts+=cyl(20,C.M3_TAP).translate([x,y,0])
    for x in (-18,18):cuts+=box(9,5,20).translate([x,-12,0])
    body-=cuts+MC.mouth_clearance()
    # 4本の座: 眼キャリアの外側。上面は頭殻実メッシュ+0.25mmで輪郭に合わせる。
    seat_clear=native(head).minkowski_sum(box(*([2*f['head_seat_gap']]*3)))
    seats=[]
    support_xy=f['head_support_xy_mm']
    support_width=float(f['head_support_width'])
    if support_width<=0 or len(support_xy)!=4:
        raise ValueError('invalid head support configuration')
    for x,y in support_xy:
            hit,_,_=head.ray.intersects_location([[x,y,4]],[[0,0,1]],multiple_hits=True)
            levels=sorted(v for v in hit[:,2] if v>4)
            if not levels:raise ValueError(f'head support misses shell at {x,y}')
            top=float(levels[0]+4)
            post=rbox(support_width,12,top,r=2).translate([x,y,top/2])-seat_clear
            # 頭の内側へ生成された、台座に繋がらない切片は支持材ではない。
            comp=post.decompose();base=[q for q in comp if q.bounding_box()[2]<.01]
            if len(base)!=1:raise ValueError('head support base disconnected')
            body+=base[0];seats.append({'xy_mm':[x,y],'first_shell_z_mm':levels[0],'generated_top_mm':top,
                                      'removed_disconnected_cut_fragments':len(comp)-1})
    # 幅20mmの面ファスナーを外周から上へ回す開放耳。上殻/下殻を同時に保持。
    for x in (-63,63):
        # Keep the outer Velcro ears, but shorten their front/back reach so
        # the 0.3 mm expanded LD case envelope cannot touch them.  The strap
        # still has a 20 mm contact band; its route is recorded in the body
        # manifest as an intentional print-first appearance change.
        y=f['head_strap_y']
        tab=rbox(12,f['head_strap_tab_depth'],t,r=2).translate([x,y,t/2])
        # The slot is 22 mm clear for the purchased 20 mm tape.  Keep the
        # cut on the outside half of each ear so the inner root remains a
        # continuous load path into the chassis.
        tab-=box(f['head_strap_slot_x'],f['head_strap_slot_width'],20).translate(
            [x+(-2 if x<0 else 2),y,2])
        body+=tab
    return body,seats

def cabin_rail(cabin,sx):
    f=C.PRINT_FIRST;x=sx*f['cabin_rail_x'];w=f['cabin_rail_width']
    z0=f['cabin_rail_z'];front=f['cabin_rail_y1'];rear=f['cabin_rail_y0']
    pad_t=float(f.get('cabin_rail_pad_t',4.0))
    pad_overlap=float(f.get('cabin_rail_pad_overlap',0.5))
    if pad_t<=0 or pad_overlap<0 or pad_overlap>=pad_t:
        raise ValueError('invalid Cabin rail pad dimensions')
    pad_top=C.CHASSIS_T+pad_t
    beam_start_z=pad_top-pad_overlap
    # 側面を下に印刷する開放三角梁。薄い首の片持ちへ荷重を集中させない。
    body=beam([x,front,beam_start_z],[x,rear,z0+4],w)
    body+=beam([x,front,beam_start_z],[x,-125,z0+4],w)
    body+=beam([x,-125,z0+4],[x,rear,z0+4],w)
    for y in (-155,-195,-235):
        ztop=8+(z0-4)*(y-front)/(rear-front)
        body+=beam([x,y,z0+4],[x,y,ztop],w)
    # 実Cabin表面で成形する広い下受け。装飾を貫く旧首は使わない。
    support=bounded([x-9,rear,z0],[x+9,-125,-24])
    cut=native(cabin).minkowski_sum(box(*([2*f['cabin_fit_gap']]*3)))
    body=(body+support)-cut
    # Chassis top z=4 through rail-pad top z=10 gives a 10 mm nominal grip
    # for the existing M3x10 screw.  The first 0.5 mm of the beam start is
    # deliberately sunk into the pad so the printed rail remains one solid.
    base=rbox(18,42,pad_t,r=3).translate([x,-74,C.CHASSIS_T+pad_t/2])
    head_depth=float(f.get('cabin_rail_screw_head_depth',1.4))
    head_d=float(f.get('cabin_rail_screw_head_d',6.4))
    tool_d=float(f.get('cabin_rail_screw_tool_clear_d',8.0))
    if not (0<head_depth<=pad_t and head_d>0 and tool_d>=head_d):
        raise ValueError('invalid Cabin rail screw head dimensions')
    for y in f['cabin_bolt_y']:
        base-=cyl(20,float(f.get('cabin_rail_clearance_d',3.2))).translate(
            [x,y,C.CHASSIS_T+pad_t/2])
        # Cylindrical counter-seat is a conservative geometric proxy for the
        # purchased countersunk head (nominal phi6.4); the real driver recess
        # and head height remain a physical fit check.
        base-=cyl(head_depth,head_d).translate(
            [x,y,C.CHASSIS_T+pad_t-head_depth/2])
    body+=base
    # 帯は梁の下を通る。2.5mm厚以下の既存20mm面ファスナーに合わせる。
    for y in (-151,-230):
        body-=box(40,21,3).translate([x,y,z0+4])
    return body

def electronics_support_specs(level, z):
    """棚上の各候補箱を四隅の3 mm印刷台座で受ける寸法を返す。

    台座は部品下面の縁だけへ置き、中央の基板/はんだ面とUSB/端子側を
    開ける。実基板の下面突起・端子位置は未測定なので、これは箱包絡に
    対する幾何候補であり、実機適合を意味しない。
    """
    f=C.PRINT_FIRST
    pad_w=float(f['electronics_support_pad_width_mm'])
    pad_d=float(f['electronics_support_pad_depth_mm'])
    inset=float(f['electronics_support_edge_inset_mm'])
    shelf_edge_clear=float(f['electronics_support_shelf_edge_clearance_mm'])
    component_overlap=float(f['electronics_support_overlap_into_component_mm'])
    central_cut_depth=float(f['electronics_support_central_cut_depth_mm'])
    base_overlap=float(f['electronics_support_base_overlap_mm'])
    if not (pad_w>0 and pad_d>0 and inset>=0 and shelf_edge_clear>=0
            and component_overlap>0 and central_cut_depth>0
            and 0<=base_overlap<float(f['deck_t'])):
        raise ValueError('invalid electronics support dimensions')
    shelf_top=float(z)+float(f['deck_t'])
    specs=[]
    for row in C.PRINT_FIRST_COMPONENTS:
        size=np.asarray(row['size_mm'],dtype=float)
        center=np.asarray(row['center_zb_mm'],dtype=float)
        if size.shape!=(3,) or center.shape!=(3,) or not np.isfinite(size).all() or not np.isfinite(center).all():
            raise ValueError(f"invalid component envelope: {row.get('name')}")
        if np.any(size<=0):
            raise ValueError(f"non-positive component envelope: {row.get('name')}")
        bottom=float(center[2]-size[2]/2.0)
        # The candidate is supported by the shelf immediately below it.
        lower_levels=[float(candidate)+float(f['deck_t'])
                      for candidate in (48.0,82.0,114.0)
                      if float(candidate)+float(f['deck_t']) <= bottom+1e-7]
        if not lower_levels or abs(max(lower_levels)-shelf_top)>1e-6:
            continue
        gap=bottom-shelf_top
        if not np.isfinite(gap) or gap<=0:
            raise ValueError(f"{row['name']}: component has no positive shelf gap")
        if not (inset+pad_w <= size[0] and inset+pad_d <= size[1]):
            raise ValueError(f"{row['name']}: edge support does not fit envelope")
        x0,x1=float(center[0]-size[0]/2.0),float(center[0]+size[0]/2.0)
        y0,y1=float(center[1]-size[1]/2.0),float(center[1]+size[1]/2.0)
        # The shelf has a central through-cut for the candidate electronics.
        # Keep the support pads outside that cut in Y, while retaining a
        # positive overlap with each candidate box's lower edge.
        cut_y0=float(f['deck_y'])-central_cut_depth/2.0
        cut_y1=float(f['deck_y'])+central_cut_depth/2.0
        lower_y=max(
            cut_y0-pad_d/2.0-shelf_edge_clear,
            y0+pad_d/2.0-component_overlap,
        )
        upper_y=min(
            cut_y1+pad_d/2.0+shelf_edge_clear,
            y1-pad_d/2.0+component_overlap,
        )
        if (lower_y+pad_d/2.0 <= y0+1e-7
                or upper_y-pad_d/2.0 >= y1-1e-7
                or lower_y-pad_d/2.0 < float(f['deck_y'])-float(f['deck_depth'])/2.0
                or upper_y+pad_d/2.0 > float(f['deck_y'])+float(f['deck_depth'])/2.0):
            raise ValueError(f"{row['name']}: shelf edge support cannot reach component edge")
        pad_centers=[
            (x0+inset+pad_w/2.0,lower_y),
            (x1-inset-pad_w/2.0,lower_y),
            (x0+inset+pad_w/2.0,upper_y),
            (x1-inset-pad_w/2.0,upper_y),
        ]
        specs.append({
            'component':row['name'], 'level':int(level),
            'component_center_mm':center.astype(float).tolist(),
            'component_size_mm':size.astype(float).tolist(),
            'component_bottom_z_mm':bottom,
            'shelf_top_z_mm':shelf_top,
            'clearance_above_shelf_mm':gap,
            'support_height_mm':gap+base_overlap,
            'support_base_overlap_mm':base_overlap,
            'pad_width_mm':pad_w, 'pad_depth_mm':pad_d,
            'edge_inset_mm':inset,
            'shelf_edge_clearance_mm':shelf_edge_clear,
            'component_edge_overlap_mm':component_overlap,
            'pad_centers_xy_mm':[[float(x),float(y)] for x,y in pad_centers],
        })
    return specs


def deck_parts():
    f=C.PRINT_FIRST;out=[]
    # 棚は同形3枚。候補箱の下面は棚上面から3 mm空け、四隅の印刷台座
    # でその空間を実体支持する。面ファスナーは補助保持として扱う。
    deck_levels=(48.,82.,114.)
    for level,z in enumerate(deck_levels):
        m=rbox(f['deck_width'],f['deck_depth'],f['deck_t'],r=3).translate([0,f['deck_y'],z+f['deck_t']/2])
        m-=rbox(f['electronics_support_central_cut_width_mm'],
                f['electronics_support_central_cut_depth_mm'],10,r=2).translate([0,f['deck_y'],z+1])
        # 板裏の足を置く5mm幅の縁。コネクタを押し潰さない帯の経路。
        for x in (-53,-18,18,53):
            for y in (-95,-65):m-=box(3,8,12).translate([x,y,z+1])
        for x in (-63,63):
            for y in f['shelf_bolt_y']:
                # M2.6 shaft clearance through the shelf; a shallow flat
                # counter-seat is sized for the nominal pan head and leaves
                # at least 2.2 mm of shelf material.
                m-=cyl(12,f['shelf_screw_clear_d']).translate([x,y,z])
                m-=cyl(f['shelf_screw_head_depth'],f['shelf_screw_head_d']).translate(
                    [x,y,z+f['deck_t']-f['shelf_screw_head_depth']/2])
        for spec in electronics_support_specs(level,z):
            # Embed only the base overlap into the shelf; the remainder is the
            # configured gap up to the component's lower envelope face.
            for x,y in spec['pad_centers_xy_mm']:
                m+=box(spec['pad_width_mm'],spec['pad_depth_mm'],spec['support_height_mm']).translate(
                    [x,y,spec['component_bottom_z_mm']-spec['support_height_mm']/2.0])
        out.append((f'pf_electronics_shelf_{level}',m))
    # 幅18mm、下面から印刷できる2つの背面柱。棚の穴径2.2mmは
    # 既購入M2.6×8タップ用であり、M3表記とは混同しない。
    for sx in (-1,1):
        x=sx*63
        body=box(6,6,118).translate([x,-104,59])
        # シャーシから柱までの三角帯。最下段から共締めでは長ねじを要求しない。
        body+=beam([sx*35,-87,8],[x,-104,44],6)
        body+=beam([sx*35,-61,8],[x,-104,44],6)
        for z in (48.,82.,114.):
            body+=box(12,44,4).translate([x,-83,z-2])
            for y in f['shelf_bolt_y']:body-=cyl(12,f['shelf_screw_tap_d']).translate([x,y,z])
        # 別体固定は既存結束バンド/面ファスナー。左右Cabin梁の外側へ当てる。
        out.append((f'pf_electronics_post_{"r" if sx>0 else "l"}',body))
    return out


def electronics_shelf_fastener_report():
    """電装棚のM2.6ねじを候補電装の占有へ通し、静的交差を記録する。

    ねじの長さは首下から先端までの寸法として扱う。0.8 mmは座ぐり深さ
    だけで頭全高ではない。残る棚2.2 mmと下側柱のタップ4 mmを通るため、
    先端は柱下面から1.8 mm出る。実なべ頭の高さと棚上突出しが3 mm以内かは
    現物確認が必要である。配線は固体メッシュで与えられていないため、
    部品の幾何交差だけをここで検査し、配線の判定は
    ``UNVERIFIED`` として台帳へ残す。
    """
    f=C.PRINT_FIRST
    screw_length=8.0
    shelf_t=float(f['deck_t'])
    post_thread=4.0
    shaft_d=float(f['shelf_screw_tap_d'])
    head_d=float(f['shelf_screw_head_d'])
    head_seat_depth=float(f['shelf_screw_head_depth'])
    head_projection_limit=float(C.ELECTRONICS_SHELF_COMPONENT_CLEARANCE_MM)
    if not (screw_length>0 and shelf_t>0 and post_thread>0 and shaft_d>0
            and head_d>0 and 0<head_seat_depth<=shelf_t
            and head_projection_limit>0):
        raise ValueError('invalid electronics shelf fastener dimensions')
    effective_shelf=max(0.0,shelf_t-head_seat_depth)
    stack=effective_shelf+post_thread
    screw_bottom_below_post=max(0.0,screw_length-stack)
    screw_engagement=max(0.0,min(post_thread,screw_length-effective_shelf))
    if screw_engagement+1e-9 < post_thread:
        raise ValueError('M2.6x8 does not reach the required 4 mm lower post')
    component_rows=A.component_meshes()
    shelf_levels=[E.ZB+level for level in (48.0,82.0,114.0)]
    component_shelf_clearances=[]
    target_clearance=float(C.ELECTRONICS_SHELF_COMPONENT_CLEARANCE_MM)
    shelf_component_names={f"component_{row['name']}" for row in A.COMPONENTS}
    for component,_,name in component_rows:
        if name not in shelf_component_names:
            continue
        low=float(component.bounds[0,2]); high=float(component.bounds[1,2])
        lower=[top+shelf_t for top in shelf_levels if top+shelf_t <= low+1e-7]
        upper=[top for top in shelf_levels if top >= high-1e-7]
        lower_top=max(lower,default=float('nan'))
        upper_bottom=min(upper,default=float('nan'))
        lower_gap=low-lower_top if np.isfinite(lower_top) else float('nan')
        upper_gap=upper_bottom-high if np.isfinite(upper_bottom) else None
        if not np.isfinite(lower_gap) or lower_gap+1e-7 < target_clearance:
            raise ValueError(f'{name}: shelf bottom clearance is below configured value: {lower_gap}')
        component_shelf_clearances.append({
            'component':name,
            'bottom_z_mm':low,
            'top_z_mm':high,
            'clearance_above_lower_shelf_mm':lower_gap,
            'clearance_below_next_shelf_mm':upper_gap,
        })
    # 各箱の下面を棚へ接続する四隅の印刷台座を、実体寸法として検査する。
    # 台座上面は箱包絡下面へ接するだけにし、下面中央/端子側を塞がない。
    support_rows=[]
    support_component_intersections=[]
    support_meshes=[]
    component_by_name={name:component for component,_,name in component_rows
                       if name in shelf_component_names}
    screw_head_clearances=[]
    head_radius=head_d/2.0
    for level,z in enumerate((48.0,82.0,114.0)):
        specs=electronics_support_specs(level,z)
        for spec in specs:
            component_name=f"component_{spec['component']}"
            if component_name not in component_by_name:
                raise ValueError(f"missing component envelope for support: {component_name}")
            component=component_by_name[component_name]
            row=dict(spec)
            row['pads']=[]
            for pad_index,(x,y) in enumerate(spec['pad_centers_xy_mm'],start=1):
                pad=trimesh.creation.box((spec['pad_width_mm'],spec['pad_depth_mm'],
                                          spec['support_height_mm']))
                pad.apply_translation([x,y,E.ZB+spec['component_bottom_z_mm']
                                       -spec['support_height_mm']/2.0])
                support_meshes.append(pad)
                # A component may only touch the pad's top face. Any positive
                # volume here would mean the nominal 3 mm gap was consumed.
                lo=np.maximum(pad.bounds[0],component.bounds[0])
                hi=np.minimum(pad.bounds[1],component.bounds[1])
                value=0.0
                if np.all(hi-lo>1e-7):
                    inter=trimesh.boolean.intersection([pad,component],engine='manifold')
                    if inter is None:
                        raise RuntimeError(f"shelf support intersection unavailable: {component_name}")
                    value=0.0 if inter.is_empty else float(inter.volume)
                if not np.isfinite(value) or value < -1e-6:
                    raise ValueError(f"shelf support intersection invalid: {component_name}: {value}")
                if value>0.01:
                    support_component_intersections.append({
                        'component':spec['component'],'pad_index':pad_index,
                        'intersection_mm3':value})
                nearest=float('inf')
                for screw_x in (-63.0,63.0):
                    for screw_y in f['shelf_bolt_y']:
                        dx=max(float(pad.bounds[0,0])-screw_x,0.0,
                               screw_x-float(pad.bounds[1,0]),0.0)
                        dy=max(float(pad.bounds[0,1])-screw_y,0.0,
                               screw_y-float(pad.bounds[1,1]),0.0)
                        nearest=min(nearest,float(np.hypot(dx,dy)-head_radius))
                if not np.isfinite(nearest):
                    raise ValueError('shelf support screw-head clearance is non-finite')
                screw_head_clearances.append(nearest)
                row['pads'].append({
                    'index':pad_index,'center_xy_mm':[float(x),float(y)],
                    'bounds_mm':pad.bounds.astype(float).tolist(),
                    'component_intersection_mm3':value,
                    'minimum_xy_clearance_to_head_candidate_mm':nearest,
                })
            support_rows.append(row)
    if not support_rows or len(support_meshes) != 4*len(support_rows):
        raise ValueError('electronics shelf has no complete edge supports')
    min_head_clearance=min(screw_head_clearances)
    if min_head_clearance <= 0.0:
        raise ValueError(f'electronics support intersects candidate screw head: {min_head_clearance}')
    intersection_pairs=[]
    checked=0
    for level,z in enumerate((48.0,82.0,114.0)):
        # M2.6x8 is measured from the underside of the head.  With
        # the nominal 0.8 mm seat, the shaft starts below the shelf
        # top by the seat depth, then extends 8 mm along -Z.
        shaft_top=z+shelf_t-head_seat_depth
        for x in (-63.0,63.0):
            for y in f['shelf_bolt_y']:
                shaft=trimesh.creation.cylinder(radius=shaft_d/2.0,
                                                 height=screw_length,
                                                 sections=48)
                shaft.apply_translation([x,y,E.ZB+shaft_top-screw_length/2.0])
                # The countersunk/pan head is represented by its maximum
                # nominal diameter at the shallow shelf seat.  The purchased
                # head profile remains a physical fit check.
                # 0.8 mm is only the shelf seat depth. The real pan-head
                # height is unmeasured, so reserve the full configured 3 mm
                # component gap as a conservative above-shelf envelope.
                head_seat=trimesh.creation.cylinder(radius=head_d/2.0,
                                                     height=head_seat_depth,
                                                     sections=48)
                head_seat.apply_translation([x,y,E.ZB+z+shelf_t-head_seat_depth/2.0])
                head_above=trimesh.creation.cylinder(radius=head_d/2.0,
                                                      height=head_projection_limit,
                                                      sections=48)
                head_above.apply_translation([x,y,E.ZB+z+shelf_t+head_projection_limit/2.0])
                envelope=trimesh.util.concatenate([shaft,head_seat,head_above])
                for component,_,name in component_rows:
                    lo=np.maximum(envelope.bounds[0],component.bounds[0])
                    hi=np.minimum(envelope.bounds[1],component.bounds[1])
                    if not np.all(hi-lo>1e-7):
                        continue
                    checked+=1
                    inter=trimesh.boolean.intersection([envelope,component],engine='manifold')
                    if inter is None:
                        raise RuntimeError(f'shelf fastener static intersection unavailable: {name}')
                    volume=0.0 if inter.is_empty else float(inter.volume)
                    if not np.isfinite(volume) or volume < -1e-6:
                        raise ValueError(f'shelf fastener static intersection invalid: {name}: {volume}')
                    if volume>0.01:
                        intersection_pairs.append({
                            'level':level,'x_mm':x,'y_mm':y,'component':name,
                            'intersection_mm3':volume,
                        })
    return {
        'screw_type':'M2.6x8_existing_stock_candidate',
        'screw_length_interpretation':'head_underside_to_tip_nominal_shank_length_mm',
        'insertion_direction':'shelf_top_to_lower_post_bottom_along_minus_Z',
        'shelf_thickness_mm':shelf_t,
        'shelf_head_seat_depth_mm':head_seat_depth,
        'effective_shelf_remaining_thickness_mm':effective_shelf,
        'lower_post_thread_length_mm':post_thread,
        'nominal_stack_mm':stack,
        'nominal_thread_engagement_mm':screw_engagement,
        'tip_protrusion_below_post_mm':screw_bottom_below_post,
        'shaft_envelope_d_mm':shaft_d,
        'head_envelope_d_mm':head_d,
        'head_seat_depth_mm':head_seat_depth,
        'head_height_candidate_mm':None,
        'head_height_candidate_basis':'actual_pan_head_height_unmeasured; 3mm component gap used only as conservative envelope limit',
        'head_projection_above_shelf_top_candidate_mm':head_projection_limit,
        'head_projection_above_shelf_top_limit_mm':head_projection_limit,
        'head_projection_limit_basis':'configured component bottom clearance; actual pan-head height must be measured on one screw',
        'configured_component_bottom_clearance_mm':target_clearance,
        'component_shelf_clearances':component_shelf_clearances,
        'component_shelf_clearance_status':'PASS',
        'support_method':'four_corner_edge_pads_per_component_with_configured_3mm_clearance',
        'support_pad_rows':support_rows,
        'support_pad_count':len(support_meshes),
        'support_component_intersections':support_component_intersections,
        'support_component_clearance_status':'PASS' if not support_component_intersections else 'FAIL',
        'support_shelf_contact_status':'PASS_CONFIGURED_TOP_TOUCH_WITH_0.2MM_BASE_OVERLAP',
        'support_minimum_xy_clearance_to_head_candidate_mm':min_head_clearance,
        'support_screw_head_clearance_status':'PASS' if min_head_clearance>0 else 'FAIL',
        'support_service_face_status':'UNVERIFIED_USB_TERMINAL_AND_SOLDER_SIDE_FROM_REAL_HARDWARE',
        'static_component_pairs_checked':checked,
        'static_component_intersections':intersection_pairs,
        'static_component_clearance_status':'PASS' if not intersection_pairs else 'FAIL',
        'wiring_clearance_status':'UNVERIFIED_WIRING_NOT_SOLID_MODELED',
        'physical_head_height_and_tap_fit_status':'UNVERIFIED_ONE_UNIT_TRIAL_REQUIRED',
    }


def cabin_rail_fastener_static_report(deck_shapes):
    """Cabin M3ねじの軸・工具包絡を電池/棚へ通して記録する。

    レールを棚より先に固定する組立順なら工具包絡は開くが、完成状態で
    上から差し込む経路は棚と候補電装に遮られる。検査結果を隠さず台帳へ
    残し、電池・候補部品のねじ軸交差と配線の未モデルを分けて記録する。
    """
    f=C.PRINT_FIRST
    pad_t=float(f.get('cabin_rail_pad_t',4.0))
    chassis_t=float(C.CHASSIS_T)
    screw_length=float(f.get('cabin_rail_screw_length',10.0))
    shaft_d=float(f.get('cabin_rail_clearance_d',3.2))
    tool_d=float(f.get('cabin_rail_screw_tool_clear_d',8.0))
    if not (pad_t>0 and chassis_t>0 and screw_length>0 and shaft_d>0 and tool_d>=shaft_d):
        raise ValueError('invalid Cabin rail fastener static dimensions')
    component_rows=A.component_meshes()
    deck_meshes={}
    for name,shape in deck_shapes.items():
        if not name.startswith('pf_electronics_shelf_'):
            continue
        mesh=to_trimesh(shape.simplify(.005))
        mesh.apply_translation([0,0,E.ZB])
        deck_meshes[name]=mesh
    if not deck_meshes:
        raise ValueError('Cabin rail static check has no electronics shelves')
    pad_top=E.ZB+chassis_t+pad_t
    tool_top=max(float(mesh.bounds[1,2]) for mesh in deck_meshes.values())+1.0
    shaft_component_intersections=[]
    tool_shelf_intersections=[]
    tool_component_intersections=[]

    def overlap_volume(a,b):
        lo=np.maximum(a.bounds[0],b.bounds[0])
        hi=np.minimum(a.bounds[1],b.bounds[1])
        if not np.all(hi-lo>1e-7):
            return 0.0
        inter=trimesh.boolean.intersection([a,b],engine='manifold')
        if inter is None:
            raise RuntimeError('Cabin rail static intersection unavailable')
        value=0.0 if inter.is_empty else float(inter.volume)
        if not np.isfinite(value) or value < -1e-6:
            raise ValueError(f'Cabin rail static intersection invalid: {value}')
        return max(0.0,value)

    for side in ('left','right'):
        x=-float(f['cabin_rail_x']) if side=='left' else float(f['cabin_rail_x'])
        for y in f['cabin_bolt_y']:
            shaft=trimesh.creation.cylinder(radius=shaft_d/2.0,height=screw_length,sections=64)
            # The candidate M3x10 is treated as the total countersunk
            # head-top-to-tip length.  Its nominal axis spans exactly from
            # the rail-pad top to the chassis underside (minus-Z).
            shaft.apply_translation([x,float(y),pad_top-screw_length/2.0])
            for component,_,name in component_rows:
                value=overlap_volume(shaft,component)
                if value>0.01:
                    shaft_component_intersections.append({
                        'side':side,'y_mm':float(y),'component':name,'intersection_mm3':value})
            tool=trimesh.creation.cylinder(radius=tool_d/2.0,height=tool_top-pad_top,sections=64)
            tool.apply_translation([x,float(y),(pad_top+tool_top)/2.0])
            for name,deck in deck_meshes.items():
                value=overlap_volume(tool,deck)
                if value>0.01:
                    tool_shelf_intersections.append({
                        'side':side,'y_mm':float(y),'shelf':name,'intersection_mm3':value})
            for component,_,name in component_rows:
                value=overlap_volume(tool,component)
                if value>0.01:
                    tool_component_intersections.append({
                        'side':side,'y_mm':float(y),'component':name,'intersection_mm3':value})
    return {
        'shaft_component_intersections':shaft_component_intersections,
        'shaft_component_clearance_status':'PASS' if not shaft_component_intersections else 'FAIL',
        'tool_envelope_d_mm':tool_d,
        'tool_envelope_world_z_mm':[pad_top,tool_top],
        'tool_shelf_intersections':tool_shelf_intersections,
        'tool_component_intersections':tool_component_intersections,
        'tool_access_with_shelves_installed_status':(
            'PASS' if not tool_shelf_intersections and not tool_component_intersections
            else 'BLOCKED_BY_SHELVES_OR_CANDIDATE_ELECTRONICS'),
        'recommended_assembly_order':'固定M3x10レールを棚/電装の取付前に行う',
        'wiring_clearance_status':'UNVERIFIED_WIRING_NOT_SOLID_MODELED',
    }

def write_part(shape,name,link='base_link',replaces=(),color='#286bb1',chassis_frame=True):
    path=OUT/(name+'.stl');mesh=to_trimesh(shape.simplify(.005))
    manifold_components=list(shape.decompose())
    manifold_volumes=[float(part.volume()) for part in manifold_components]
    if any(not np.isfinite(volume) for volume in manifold_volumes):
        raise ValueError(f'{name}: non-finite component volume')
    negative=[volume for volume in manifold_volumes if volume < -1e-8]
    if negative:
        raise ValueError(f'{name}: negative-volume component(s) would be discarded: {negative}')
    positive=[volume for volume in manifold_volumes if volume > 1e-8]
    if len(positive)!=1:
        raise ValueError(f'{name}: {len(positive)} positive components')
    if not (mesh.is_watertight and mesh.is_winding_consistent and np.isfinite(mesh.volume)
            and mesh.volume > 0):
        raise ValueError(f'{name}: STL前の閉形状/向き/体積検査失敗')
    mesh.export(path);actual=trimesh.load(path,force='mesh')
    if (not actual.is_watertight or not actual.is_winding_consistent
            or not np.isfinite(actual.volume) or actual.volume <= 0 or not actual.is_volume):
        raise ValueError(f'{name}: invalid serialized solid')
    T=E.trans(0,0,E.ZB) if chassis_frame else np.eye(4)
    return {'name':name,'stl':str(path.relative_to(ROOT)),'exists':True,
            'link':link,'transform':T.tolist(),
            'replaces':list(replaces),'color':color,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'bbox_mm':actual.extents.tolist(),'volume_mm3':float(actual.volume),
            'solid_pla_g_upper_bound':float(actual.volume)*C.material_density_g_cm3('PLA')/1000,
            'watertight':bool(actual.is_watertight),
            'winding_consistent':bool(actual.is_winding_consistent),
            'positive_components':1,'is_volume':bool(actual.is_volume)}


def generated_part_names():
    """Return the exact STL names emitted by ``main``.

    The electronics posts are deliberately consumed by the cabin rails, so
    they are not standalone outputs.  Keep this naming contract next to the
    rows that ``main`` writes so a stage allowlist cannot drift silently.
    """
    names = [
        'pf_chassis',
        'pf_head_top_clearanced',
        *(f'pf_ld220_yaw_cap_{leg.lower()}' for leg in C.HIPS),
        'pf_cabin_rail_l',
        'pf_cabin_rail_r',
        'pf_electronics_shelf_0',
        'pf_electronics_shelf_1',
        'pf_electronics_shelf_2',
        'pf_mouth_key',
        'pf_camera_carrier',
        'pf_eye_pod_camera_clearanced',
        'pf_fixed_claw_r',
        'pf_fixed_claw_l',
    ]
    if len(names) != len(set(names)):
        raise RuntimeError(f'duplicate print-first body output name: {names!r}')
    return tuple(names)


def yaw_cap_parts():
    """4個のヨーLDケース蓋をbodyローカル座標へ展開する。"""
    p=D.Candidate();f=C.PRINT_FIRST;rows=[]
    for leg,(x,y) in C.HIPS.items():
        a=float(f['yaw_case_angles'][leg])
        raw=D.cap(p).translate([0,0,-p.main_projection])
        T=(PL._translation(x,y,PL.yaw_face_z()-E.ZB)
           @ PL._rotation(a,'z') @ PL._rotation(180,'x'))
        rows.append((leg,PL._xform(raw,T)))
    return rows

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assembly_path=OUT/'assembly.json'
    stale_marker=OUT/'assembly.json.incomplete'
    stale_marker.write_text(
        json.dumps({'status':'GENERATION_INCOMPLETE_STALE_ASSEMBLY_MUST_NOT_BE_USED',
                    'generator':str(Path(__file__).relative_to(ROOT))},
                   ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    with A.context(generated=False):
        parts=E.collect_all_parts()['base_link']
        head=next(m.copy() for m,c,n in parts if n.startswith('Head_Top'))
        # A.context() already applies PRINT_FIRST.head_top_lift through
        # E._LINK_T_STATIC before collect_all_parts().  Remove ZB only to
        # enter the body generator's local seat frame; applying the lift a
        # second time here would move the support seats twice.
        head.apply_translation([0,0,-E.ZB])
        # Build the final camera carrier from the same camera-local holder
        # geometry used by the collision/mass path. The source carrier remains
        # untouched; this is the only carrier row in the generated assembly.
        carrier, holder, carrier_clearance = _build_pf_camera_carrier(head)
        head_obstacles = _head_clearance_obstacles(head, carrier, holder)
        head_clearanced, head_clearance = _clearance_difference(
            head, head_obstacles, label='pf_head_top_clearanced')
        head_clearanced_mesh = to_trimesh(head_clearanced.simplify(.005))
        if (not head_clearanced_mesh.is_watertight
                or not head_clearanced_mesh.is_winding_consistent
                or not head_clearanced_mesh.is_volume
                or not np.isfinite(head_clearanced_mesh.volume)
                or head_clearanced_mesh.volume <= 0.0):
            raise ValueError(
                'pf_head_top_clearanced: chassis input is not a serialized solid')
        # Keep the old white pod as a source reference and generate a single
        # largest valid replacement with the lifted XIAO board, fixed lens,
        # and final carrier escaped by the same 0.2 mm candidate clearance.
        pod_source = E.camera_link_parts()[0][0]
        pod_obstacles = [
            ('xiao_all_boards_occupancy', holder['xiao_all_boards_occupancy']),
            ('camera_child_lens_occupancy', holder['camera_child_lens_occupancy']),
            ('pf_camera_carrier', carrier),
        ]
        pod_clearanced, pod_clearance = _clearance_difference(
            pod_source, pod_obstacles, label='pf_eye_pod_camera_clearanced')
        cabin=trimesh.util.concatenate([m.copy().apply_translation([0,0,-E.ZB]) for m,c,n in parts if n in C.CABIN_POSES])
        ch,seats=chassis(head_clearanced_mesh)
        rows=[write_part(ch,'pf_chassis',replaces=['chassis'])]
        rows.append(write_part(
            head_clearanced, 'pf_head_top_clearanced', link='base_link',
            replaces=['Head_Top_Eyecut#single'], color='#2d55b8',
            chassis_frame=True))
        # ヨーケース蓋はシャーシ本体へ一体化せず、サーボ挿入後に
        # 既購入M3×10×2本で締める交換部品として保持する。
        for leg,cap_shape in yaw_cap_parts():
            rows.append(write_part(cap_shape,f'pf_ld220_yaw_cap_{leg.lower()}',
                                   color='#607d8b',chassis_frame=True))
        decks=dict(deck_parts())
        for sx in (-1,1):
            tag='r' if sx>0 else 'l'
            rows.append(write_part((cabin_rail(cabin,sx)+decks.pop(f'pf_electronics_post_{tag}'))-ch,f'pf_cabin_rail_{tag}'))
        for name,shape in decks.items():rows.append(write_part(shape,name))
        cabin_fastener_static=cabin_rail_fastener_static_report(decks)
        # 既存口キー/カメラ受けの微小交差に0.2mm逃げを追加。元部品は保持。
        key=next(m for m,c,n in parts if n.startswith('Mouth_Key_'))
        obstacles=[native(m) for m,c,n in parts if n in ('Mouth_Cannon_Grey','Mouth_Cap_Grey#single')]
        fitted=native(key)
        for ob in obstacles:fitted-=ob.minkowski_sum(box(.4,.4,.4))
        rows.append(write_part(fitted,'pf_mouth_key',replaces=['Mouth_Key_Grey#single'],color='#888888',chassis_frame=False))
        carrier_row=write_part(carrier,'pf_camera_carrier',link='eye_pod_camera',
                               replaces=['camera_carrier'],color='#888888',chassis_frame=False)
        carrier_row['xiao_holder_integration']={
            'variant':C.PRINT_FIRST_XIAO['variant'],
            'candidate_status':C.PRINT_FIRST_XIAO['status'],
            **carrier_clearance,
            'holder_z_offset_mm':float(C.PRINT_FIRST_XIAO['holder_z_offset_mm']),
            'board_and_camera_occupancy_remains_separate_collision_candidate':True,
            'fpc_integration':A.xiao_fpc_summary(),
            'retention':A.xiao_retention_summary(holder),
            'physical_fit_status':'UNVERIFIED_ONE_UNIT_TRIAL_REQUIRED',
        }
        rows.append(carrier_row)
        pod_row=write_part(
            pod_clearanced, 'pf_eye_pod_camera_clearanced',
            link='eye_pod_camera', replaces=['eye_pod_camera'],
            color='#f4f3f0', chassis_frame=False)
        pod_row['clearance_integration']=pod_clearance
        pod_row['physical_fit_status']='UNVERIFIED_ONE_UNIT_TRIAL_REQUIRED'
        rows.append(pod_row)
        # 接着位置が食い違う小さな固定爪は一体化。軸・前腕・ホーンはそのまま。
        for side in ('r','l'):
            ps=E.collect_all_parts()[f'arm_{side}_forearm']
            names=[n for m,c,n in ps if n.startswith(('Arm_Left_Claw','Arm_Left_Finger','claw_mount'))]
            meshes=[native(m) for m,c,n in ps if n in names]
            rows.append(write_part(Manifold.batch_boolean(meshes,__import__('manifold3d').OpType.Add),f'pf_fixed_claw_{side}',
                                   link=f'arm_{side}_forearm',replaces=names,color='#555555',chassis_frame=False))
        actual_names = tuple(row['name'] for row in rows)
        expected_names = generated_part_names()
        if actual_names != expected_names:
            raise RuntimeError(
                f'print-first body output names differ: expected={expected_names!r}, '
                f'actual={actual_names!r}')
        rail_pad_t=float(C.PRINT_FIRST.get('cabin_rail_pad_t',4.0))
        chassis_t=float(C.CHASSIS_T)
        rail_screw_length=float(C.PRINT_FIRST.get('cabin_rail_screw_length',10.0))
        source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (Path(__file__).resolve(), ROOT/'hardware/src/config.py',
                                   ROOT/'tools/print_first_assembly.py',
                                   ROOT/'tools/print_first_components.py',
                                   ROOT/'tools/xiao_retention_plan.py',
                                   ROOT/'tools/export_urdf.py',
                                   ROOT/'hardware/src/make_ld220_adapter.py',
                                   ROOT/'hardware/src/make_print_first_leg.py',
                                   ROOT/'docs/audits/20260905-round2/xiao-retention-plan.json')}
        for row in rows:
            row['exists'] = True
            row['source'] = 'hardware/src/make_print_first_body.py'
            row['source_sha256'] = source_hashes['hardware/src/make_print_first_body.py']
        result={'status':'GEOMETRY_GENERATED_NOT_YET_FULL_ASSEMBLY_VALIDATED','config':C.PRINT_FIRST,'parts':rows,
                'head_seats':seats,'stored_decorative_prefixes':A.STORED_PREFIXES,
                'cabin_storage_policy':A.cabin_storage_policy_record(),
                'head_clearance':head_clearance,
                'eye_pod_camera_clearance':pod_clearance,
                'pf_camera_carrier_clearance':carrier_clearance,
                'electronics_shelf_fastener':electronics_shelf_fastener_report(),
                'cabin_rail_fastener':{
                    'screw_type':'M3x10_existing_stock_candidate',
                    'screw_length_interpretation':'countersunk_total_head_top_to_tip_candidate',
                    'insertion_direction':'rail_top_to_chassis_bottom_along_minus_Z',
                    'rail_pad_thickness_mm':rail_pad_t,
                    'chassis_thickness_mm':chassis_t,
                    'nominal_grip_mm':rail_pad_t+chassis_t,
                    'screw_length_mm':rail_screw_length,
                    'nominal_chassis_thread_engagement_mm':max(0.0,min(chassis_t,rail_screw_length-rail_pad_t)),
                    'thread_engagement_shortfall_mm':max(0.0,chassis_t-max(0.0,rail_screw_length-rail_pad_t)),
                    'tip_protrusion_below_chassis_mm':max(0.0,rail_screw_length-(rail_pad_t+chassis_t)),
                    'rail_clearance_d_mm':float(C.PRINT_FIRST.get('cabin_rail_clearance_d',3.2)),
                    'countersunk_head_d_mm':float(C.PRINT_FIRST.get('cabin_rail_screw_head_d',6.4)),
                    'countersunk_head_depth_mm':float(C.PRINT_FIRST.get('cabin_rail_screw_head_depth',1.4)),
                    'tool_envelope_d_mm':float(C.PRINT_FIRST.get('cabin_rail_screw_tool_clear_d',8.0)),
                    'tip_battery_wiring_clearance_status':'GEOMETRIC_CANDIDATE_REQUIRES_STATIC_ASSEMBLY_CHECK',
                    'physical_head_axis_and_driver_fit_status':'UNVERIFIED',
                    'static_assembly_check':cabin_fastener_static,
                },
                'source_sha256':source_hashes,
                'source_files':[{'path':path,'exists':True,'sha256':digest}
                                for path,digest in source_hashes.items()]}
        temporary=OUT/'.assembly.json.tmp'
        temporary.write_text(
            json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',
            encoding='utf-8')
        temporary.replace(assembly_path)
        stale_marker.unlink(missing_ok=True)
        print(json.dumps({'parts':len(rows),'solid_pla_g_upper_bound':sum(p['solid_pla_g_upper_bound'] for p in rows)},ensure_ascii=False))
if __name__=='__main__':main()
